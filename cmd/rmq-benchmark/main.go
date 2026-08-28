package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"runtime"
	"sort"
	"strings"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

type check struct {
	Name     string         `json:"name"`
	Passed   bool           `json:"passed"`
	Observed map[string]any `json:"observed"`
}

type cell struct {
	QueueType          string  `json:"queue_type"`
	Messages           int     `json:"messages"`
	PayloadBytes       int     `json:"payload_bytes"`
	PublishSeconds     float64 `json:"publish_seconds"`
	ConsumeSeconds     float64 `json:"consume_seconds"`
	PublishPerSecond   float64 `json:"publish_per_second"`
	ConsumePerSecond   float64 `json:"consume_per_second"`
	ConfirmP50Micros   int64   `json:"confirm_p50_micros"`
	ConfirmP95Micros   int64   `json:"confirm_p95_micros"`
	ConfirmP99Micros   int64   `json:"confirm_p99_micros"`
	ConfirmLatencyRaw  []int64 `json:"confirm_latency_micros"`
	Published          int     `json:"published"`
	Confirmed          int     `json:"confirmed"`
	Consumed           int     `json:"consumed"`
	UniqueMessageIDs   int     `json:"unique_message_ids"`
	BrokerMemoryBefore int64   `json:"broker_memory_bytes_before"`
	BrokerMemoryAfter  int64   `json:"broker_memory_bytes_after"`
}

type report struct {
	SchemaVersion int            `json:"schema_version"`
	Mode          string         `json:"mode"`
	RunID         string         `json:"run_id"`
	CreatedAt     string         `json:"created_at"`
	Environment   map[string]any `json:"environment"`
	Workload      map[string]any `json:"workload"`
	Cells         []cell         `json:"cells"`
	Checks        []check        `json:"checks"`
}

func main() {
	amqpURLs := flag.String("amqp-urls", "amqp://atlas:atlas-local-only@127.0.0.1:25672/", "comma-separated AMQP endpoints")
	managementURLs := flag.String("management-urls", "http://127.0.0.1:35672", "comma-separated Management endpoints")
	messages := flag.Int("messages", 300, "messages per queue type")
	payloadBytes := flag.Int("payload-bytes", 1024, "payload size")
	output := flag.String("output", "", "JSON output path")
	flag.Parse()
	result, err := run(split(*amqpURLs), split(*managementURLs), *messages, *payloadBytes)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		panic(err)
	}
	data = append(data, '\n')
	if *output == "" {
		_, err = os.Stdout.Write(data)
	} else {
		err = os.WriteFile(*output, data, 0o644)
	}
	if err != nil {
		panic(err)
	}
}

func run(endpoints, management []string, count, payloadSize int) (report, error) {
	if count < 10 || payloadSize < 1 {
		return report{}, errors.New("messages must be >= 10 and payload-bytes must be positive")
	}
	version, err := brokerVersion(management)
	if err != nil {
		return report{}, err
	}
	if version != "4.3.5" {
		return report{}, fmt.Errorf("expected RabbitMQ 4.3.5, got %s", version)
	}
	conn, err := connect(endpoints)
	if err != nil {
		return report{}, err
	}
	defer conn.Close()
	runID := fmt.Sprintf("%d", time.Now().UTC().UnixNano())
	r := report{SchemaVersion: 1, Mode: "fixed-workload-performance", RunID: runID, CreatedAt: time.Now().UTC().Format(time.RFC3339), Environment: map[string]any{"rabbitmq_version": version, "amqp_client": "github.com/rabbitmq/amqp091-go@v1.10.0", "go_version": runtime.Version(), "goos": runtime.GOOS, "goarch": runtime.GOARCH, "logical_cpu": runtime.NumCPU(), "gomaxprocs": runtime.GOMAXPROCS(0)}, Workload: map[string]any{"messages_per_cell": count, "payload_bytes": payloadSize, "publisher_channels": 1, "consumers": 1, "publisher_confirms": true, "manual_ack": true, "delivery_mode": "persistent", "queue_types": []string{"classic", "quorum", "stream"}, "scope": "local fixed-condition measurement; no portable capacity or universal queue-type ranking"}}
	for _, queueType := range []string{"classic", "quorum", "stream"} {
		measured, err := benchmarkCell(conn, management, runID, queueType, count, payloadSize)
		if err != nil {
			return report{}, fmt.Errorf("%s cell: %w", queueType, err)
		}
		r.Cells = append(r.Cells, measured)
		passed := measured.Published == count && measured.Confirmed == count && measured.Consumed == count && measured.UniqueMessageIDs == count && measured.ConfirmP50Micros > 0 && measured.ConfirmP95Micros >= measured.ConfirmP50Micros && measured.ConfirmP99Micros >= measured.ConfirmP95Micros && measured.BrokerMemoryBefore > 0 && measured.BrokerMemoryAfter > 0
		r.Checks = append(r.Checks, check{Name: "performance." + queueType, Passed: passed, Observed: map[string]any{"published": measured.Published, "confirmed": measured.Confirmed, "consumed": measured.Consumed, "unique_message_ids": measured.UniqueMessageIDs, "confirm_p50_micros": measured.ConfirmP50Micros, "confirm_p95_micros": measured.ConfirmP95Micros, "confirm_p99_micros": measured.ConfirmP99Micros, "broker_memory_before": measured.BrokerMemoryBefore, "broker_memory_after": measured.BrokerMemoryAfter}})
		if !passed {
			return report{}, fmt.Errorf("%s performance measurement invariant failed", queueType)
		}
	}
	return r, nil
}

func benchmarkCell(conn *amqp.Connection, management []string, runID, queueType string, count, payloadSize int) (cell, error) {
	ch, err := conn.Channel()
	if err != nil {
		return cell{}, err
	}
	defer ch.Close()
	args := amqp.Table{}
	if queueType != "classic" {
		args["x-queue-type"] = queueType
	}
	if queueType == "quorum" {
		args["x-quorum-initial-group-size"] = int32(3)
	}
	q, err := ch.QueueDeclare("atlas."+runID+".performance."+queueType, true, false, false, false, args)
	if err != nil {
		return cell{}, err
	}
	defer ch.QueueDelete(q.Name, false, false, false)
	if err := ch.Confirm(false); err != nil {
		return cell{}, err
	}
	if err := ch.Qos(100, 0, false); err != nil {
		return cell{}, err
	}
	memoryBefore, err := brokerMemory(management)
	if err != nil {
		return cell{}, err
	}
	payload := make([]byte, payloadSize)
	for index := range payload {
		payload[index] = byte(index % 251)
	}
	latencies := make([]int64, 0, count)
	publishStart := time.Now()
	confirmed := 0
	for i := 0; i < count; i++ {
		start := time.Now()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		confirmation, err := ch.PublishWithDeferredConfirmWithContext(ctx, "", q.Name, false, false, amqp.Publishing{DeliveryMode: amqp.Persistent, ContentType: "application/octet-stream", MessageId: fmt.Sprintf("%s.%s.%06d", runID, queueType, i), Body: payload})
		if err == nil && confirmation != nil {
			var ack bool
			ack, err = confirmation.WaitContext(ctx)
			if ack {
				confirmed++
			} else if err == nil {
				err = errors.New("publisher confirm nack")
			}
		}
		cancel()
		if err != nil {
			return cell{}, err
		}
		latencies = append(latencies, time.Since(start).Microseconds())
	}
	publishDuration := time.Since(publishStart)
	consumerArgs := amqp.Table{}
	if queueType == "stream" {
		consumerArgs["x-stream-offset"] = "first"
	}
	tag := "atlas-performance-" + queueType
	deliveries, err := ch.Consume(q.Name, tag, false, false, false, false, consumerArgs)
	if err != nil {
		return cell{}, err
	}
	consumeStart := time.Now()
	unique := map[string]bool{}
	consumed := 0
	deadline := time.After(60 * time.Second)
	for consumed < count {
		select {
		case delivery, ok := <-deliveries:
			if !ok {
				return cell{}, errors.New("consumer channel closed")
			}
			unique[delivery.MessageId] = true
			consumed++
			if err := delivery.Ack(false); err != nil {
				return cell{}, err
			}
		case <-deadline:
			return cell{}, errors.New("consume timeout")
		}
	}
	consumeDuration := time.Since(consumeStart)
	_ = ch.Cancel(tag, false)
	memoryAfter, err := brokerMemory(management)
	if err != nil {
		return cell{}, err
	}
	sorted := append([]int64(nil), latencies...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	return cell{QueueType: queueType, Messages: count, PayloadBytes: payloadSize, PublishSeconds: publishDuration.Seconds(), ConsumeSeconds: consumeDuration.Seconds(), PublishPerSecond: float64(count) / publishDuration.Seconds(), ConsumePerSecond: float64(count) / consumeDuration.Seconds(), ConfirmP50Micros: percentile(sorted, 50), ConfirmP95Micros: percentile(sorted, 95), ConfirmP99Micros: percentile(sorted, 99), ConfirmLatencyRaw: latencies, Published: count, Confirmed: confirmed, Consumed: consumed, UniqueMessageIDs: len(unique), BrokerMemoryBefore: memoryBefore, BrokerMemoryAfter: memoryAfter}, nil
}

func percentile(values []int64, p int) int64 {
	index := (len(values)*p + 99) / 100
	if index < 1 {
		index = 1
	}
	return values[index-1]
}

func connect(endpoints []string) (*amqp.Connection, error) {
	var last error
	for _, endpoint := range endpoints {
		conn, err := amqp.DialConfig(endpoint, amqp.Config{Heartbeat: 5 * time.Second, Dial: amqp.DefaultDial(5 * time.Second)})
		if err == nil {
			return conn, nil
		}
		last = err
	}
	return nil, last
}

func brokerVersion(endpoints []string) (string, error) {
	var overview struct {
		Version string `json:"rabbitmq_version"`
	}
	if err := managementGet(endpoints, "/api/overview", &overview); err != nil {
		return "", err
	}
	return overview.Version, nil
}

func brokerMemory(endpoints []string) (int64, error) {
	var nodes []struct {
		Memory int64 `json:"mem_used"`
	}
	if err := managementGet(endpoints, "/api/nodes", &nodes); err != nil {
		return 0, err
	}
	var total int64
	for _, node := range nodes {
		total += node.Memory
	}
	return total, nil
}

func managementGet(endpoints []string, path string, target any) error {
	client := &http.Client{Timeout: 10 * time.Second}
	var last error
	for _, endpoint := range endpoints {
		req, err := http.NewRequest(http.MethodGet, strings.TrimRight(endpoint, "/")+path, nil)
		if err != nil {
			return err
		}
		req.SetBasicAuth("atlas", "atlas-local-only")
		resp, err := client.Do(req)
		if err != nil {
			last = err
			continue
		}
		if resp.StatusCode == http.StatusOK {
			err = json.NewDecoder(resp.Body).Decode(target)
			resp.Body.Close()
			if err == nil {
				return nil
			}
			last = err
			continue
		}
		last = fmt.Errorf("management status %s", resp.Status)
		resp.Body.Close()
	}
	return last
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
