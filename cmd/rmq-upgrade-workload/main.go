package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

type phaseStats struct {
	Confirmed int `json:"confirmed"`
	Received  int `json:"received"`
	Errors    int `json:"errors"`
}

type workloadError struct {
	At      string `json:"at"`
	Phase   string `json:"phase"`
	Message string `json:"message"`
}

type report struct {
	SchemaVersion       int                    `json:"schema_version"`
	StartedAt           string                 `json:"started_at"`
	FinishedAt          string                 `json:"finished_at"`
	Queue               string                 `json:"queue"`
	Endpoints           []string               `json:"endpoints"`
	ConfirmedIDs        []string               `json:"confirmed_ids"`
	ReceivedIDs         []string               `json:"received_ids"`
	MissingConfirmed    []string               `json:"missing_confirmed_ids"`
	DuplicateDeliveries int                    `json:"duplicate_deliveries"`
	Reconnects          int                    `json:"reconnects"`
	PhaseStats          map[string]*phaseStats `json:"phase_stats"`
	Errors              []workloadError        `json:"errors"`
}

type runner struct {
	endpoints  []string
	queue      string
	phaseFile  string
	stopFile   string
	readyFile  string
	output     string
	started    time.Time
	confirmed  map[string]bool
	received   map[string]int
	phases     map[string]*phaseStats
	errors     []workloadError
	reconnects int
	sequence   int
}

func main() {
	endpointList := flag.String("amqp-urls", "", "comma-separated AMQP endpoints")
	queue := flag.String("queue", "atlas.upgrade.quorum", "durable quorum queue")
	phaseFile := flag.String("phase-file", "", "file containing the current upgrade phase")
	stopFile := flag.String("stop-file", "", "existence requests a graceful stop")
	readyFile := flag.String("ready-file", "", "created after topology declaration")
	output := flag.String("output", "", "JSON result path")
	maxDuration := flag.Duration("max-duration", 8*time.Minute, "safety time limit")
	flag.Parse()

	endpoints := split(*endpointList)
	if len(endpoints) == 0 || *phaseFile == "" || *stopFile == "" || *readyFile == "" || *output == "" {
		fmt.Fprintln(os.Stderr, "amqp-urls, phase-file, stop-file, ready-file and output are required")
		os.Exit(2)
	}
	r := &runner{
		endpoints: endpoints,
		queue:     *queue,
		phaseFile: *phaseFile,
		stopFile:  *stopFile,
		readyFile: *readyFile,
		output:    *output,
		started:   time.Now().UTC(),
		confirmed: map[string]bool{},
		received:  map[string]int{},
		phases:    map[string]*phaseStats{},
	}
	deadline := time.Now().Add(*maxDuration)
	for time.Now().Before(deadline) && !exists(*stopFile) {
		if err := r.runSession(deadline); err != nil {
			r.recordError(err)
			time.Sleep(300 * time.Millisecond)
		}
	}
	if !exists(*stopFile) {
		r.recordError(errors.New("safety time limit reached before stop request"))
	}
	r.drain(30 * time.Second)
	if err := r.writeReport(); err != nil {
		fmt.Fprintf(os.Stderr, "write workload report: %v\n", err)
		os.Exit(1)
	}
}

func split(value string) []string {
	var result []string
	for _, item := range strings.Split(value, ",") {
		if item = strings.TrimSpace(item); item != "" {
			result = append(result, item)
		}
	}
	return result
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func (r *runner) phase() string {
	content, err := os.ReadFile(r.phaseFile)
	if err != nil {
		return "unknown"
	}
	phase := strings.TrimSpace(string(content))
	if phase == "" {
		return "unknown"
	}
	return phase
}

func (r *runner) stats(phase string) *phaseStats {
	if r.phases[phase] == nil {
		r.phases[phase] = &phaseStats{}
	}
	return r.phases[phase]
}

func (r *runner) connect(deadline time.Time) (*amqp.Connection, string, error) {
	var last error
	for time.Now().Before(deadline) {
		for _, endpoint := range r.endpoints {
			connection, err := amqp.DialConfig(endpoint, amqp.Config{
				Heartbeat: 5 * time.Second,
				Dial:      amqp.DefaultDial(3 * time.Second),
			})
			if err == nil {
				return connection, endpoint, nil
			}
			last = err
		}
		time.Sleep(300 * time.Millisecond)
	}
	if last == nil {
		last = errors.New("connection deadline reached")
	}
	return nil, "", last
}

func (r *runner) runSession(deadline time.Time) error {
	connection, _, err := r.connect(deadline)
	if err != nil {
		return err
	}
	r.reconnects++
	defer connection.Close()
	channel, err := connection.Channel()
	if err != nil {
		return err
	}
	defer channel.Close()
	if _, err := channel.QueueDeclare(r.queue, true, false, false, false, amqp.Table{
		"x-queue-type":                "quorum",
		"x-quorum-initial-group-size": int32(3),
	}); err != nil {
		return fmt.Errorf("declare quorum queue: %w", err)
	}
	if err := channel.Qos(10, 0, false); err != nil {
		return fmt.Errorf("set qos: %w", err)
	}
	if err := channel.Confirm(false); err != nil {
		return fmt.Errorf("enable confirms: %w", err)
	}
	if !exists(r.readyFile) {
		if err := os.WriteFile(r.readyFile, []byte(time.Now().UTC().Format(time.RFC3339Nano)+"\n"), 0o600); err != nil {
			return err
		}
	}

	for time.Now().Before(deadline) && !exists(r.stopFile) {
		phase := r.phase()
		messageID := fmt.Sprintf("upgrade-%06d", r.sequence)
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		confirmation, err := channel.PublishWithDeferredConfirmWithContext(ctx, "", r.queue, true, false, amqp.Publishing{
			DeliveryMode: amqp.Persistent,
			ContentType:  "text/plain",
			MessageId:    messageID,
			Timestamp:    time.Now().UTC(),
			Body:         []byte(messageID),
		})
		if err != nil {
			cancel()
			return fmt.Errorf("publish %s: %w", messageID, err)
		}
		acknowledged, err := confirmation.WaitContext(ctx)
		cancel()
		if err != nil {
			return fmt.Errorf("wait confirm %s: %w", messageID, err)
		}
		if !acknowledged {
			return fmt.Errorf("publish negatively acknowledged: %s", messageID)
		}
		r.confirmed[messageID] = true
		r.stats(phase).Confirmed++
		r.sequence++

		delivery, ok, err := channel.Get(r.queue, false)
		if err != nil {
			return fmt.Errorf("consume: %w", err)
		}
		if ok {
			if err := delivery.Ack(false); err != nil {
				return fmt.Errorf("ack %s: %w", delivery.MessageId, err)
			}
			r.received[delivery.MessageId]++
			r.stats(phase).Received++
		}
		time.Sleep(100 * time.Millisecond)
	}
	return nil
}

func (r *runner) drain(limit time.Duration) {
	deadline := time.Now().Add(limit)
	quietSince := time.Now()
	for time.Now().Before(deadline) {
		if r.allConfirmedReceived() && time.Since(quietSince) > time.Second {
			return
		}
		connection, _, err := r.connect(time.Now().Add(5 * time.Second))
		if err != nil {
			r.recordError(fmt.Errorf("drain connect: %w", err))
			return
		}
		channel, err := connection.Channel()
		if err != nil {
			connection.Close()
			r.recordError(fmt.Errorf("drain channel: %w", err))
			continue
		}
		for {
			delivery, ok, getErr := channel.Get(r.queue, false)
			if getErr != nil || !ok {
				if getErr != nil {
					r.recordError(fmt.Errorf("drain consume: %w", getErr))
				}
				break
			}
			if ackErr := delivery.Ack(false); ackErr != nil {
				r.recordError(fmt.Errorf("drain ack: %w", ackErr))
				break
			}
			r.received[delivery.MessageId]++
			r.stats(r.phase()).Received++
			quietSince = time.Now()
		}
		channel.Close()
		connection.Close()
		time.Sleep(200 * time.Millisecond)
	}
}

func (r *runner) allConfirmedReceived() bool {
	for id := range r.confirmed {
		if r.received[id] == 0 {
			return false
		}
	}
	return true
}

func (r *runner) recordError(err error) {
	phase := r.phase()
	r.stats(phase).Errors++
	if len(r.errors) < 200 {
		r.errors = append(r.errors, workloadError{
			At:      time.Now().UTC().Format(time.RFC3339Nano),
			Phase:   phase,
			Message: err.Error(),
		})
	}
}

func sortedBoolKeys(values map[string]bool) []string {
	result := make([]string, 0, len(values))
	for key := range values {
		result = append(result, key)
	}
	sort.Strings(result)
	return result
}

func sortedIntKeys(values map[string]int) []string {
	result := make([]string, 0, len(values))
	for key := range values {
		result = append(result, key)
	}
	sort.Strings(result)
	return result
}

func (r *runner) writeReport() error {
	missing := []string{}
	duplicates := 0
	for id := range r.confirmed {
		if r.received[id] == 0 {
			missing = append(missing, id)
		}
	}
	for _, count := range r.received {
		if count > 1 {
			duplicates += count - 1
		}
	}
	sort.Strings(missing)
	result := report{
		SchemaVersion:       1,
		StartedAt:           r.started.Format(time.RFC3339Nano),
		FinishedAt:          time.Now().UTC().Format(time.RFC3339Nano),
		Queue:               r.queue,
		Endpoints:           r.endpoints,
		ConfirmedIDs:        sortedBoolKeys(r.confirmed),
		ReceivedIDs:         sortedIntKeys(r.received),
		MissingConfirmed:    missing,
		DuplicateDeliveries: duplicates,
		Reconnects:          r.reconnects,
		PhaseStats:          r.phases,
		Errors:              r.errors,
	}
	content, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	content = append(content, '\n')
	return os.WriteFile(r.output, content, 0o644)
}
