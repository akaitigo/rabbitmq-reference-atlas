package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

type check struct {
	Name     string         `json:"name"`
	Passed   bool           `json:"passed"`
	Observed map[string]any `json:"observed"`
}

type report struct {
	SchemaVersion int     `json:"schema_version"`
	Mode          string  `json:"mode"`
	RunID         string  `json:"run_id"`
	CreatedAt     string  `json:"created_at"`
	Checks        []check `json:"checks"`
}

func main() {
	amqpURL := flag.String("amqp-url", "amqp://atlas:atlas-local-only@127.0.0.1:25672/", "AMQP endpoint")
	managementURL := flag.String("management-url", "http://127.0.0.1:35672", "Management endpoint")
	expectBlocked := flag.Bool("expect-blocked", false, "expect resource alarm and blocked publisher")
	output := flag.String("output", "", "JSON output")
	flag.Parse()
	r, err := run(*amqpURL, *managementURL, *expectBlocked)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	data, _ := json.MarshalIndent(r, "", "  ")
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

func run(endpoint, management string, expectBlocked bool) (report, error) {
	runID := fmt.Sprintf("%d", time.Now().UTC().UnixNano())
	mode := "resumed"
	if expectBlocked {
		mode = "blocked"
	}
	r := report{SchemaVersion: 1, Mode: "publisher-flow-" + mode, RunID: runID, CreatedAt: time.Now().UTC().Format(time.RFC3339)}
	alarmStatus, err := waitAlarmState(management, expectBlocked, 30*time.Second)
	if err != nil {
		return r, err
	}
	conn, err := amqp.DialConfig(endpoint, amqp.Config{Heartbeat: 3 * time.Second, Dial: amqp.DefaultDial(5 * time.Second)})
	if err != nil {
		return r, err
	}
	blockedEvents := conn.NotifyBlocked(make(chan amqp.Blocking, 2))
	ch, err := conn.Channel()
	if err != nil {
		return r, err
	}
	q, err := ch.QueueDeclare("atlas."+runID+".publisher-flow", false, true, true, false, nil)
	if err != nil {
		return r, err
	}
	if err := ch.Confirm(false); err != nil {
		return r, err
	}
	type publishOutcome struct {
		confirmed bool
		err       error
	}
	outcomes := make(chan publishOutcome, 1)
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
		defer cancel()
		confirmation, err := ch.PublishWithDeferredConfirmWithContext(ctx, "", q.Name, false, false, amqp.Publishing{DeliveryMode: amqp.Persistent, MessageId: runID, Body: []byte("publisher-flow-control-probe")})
		if err != nil || confirmation == nil {
			outcomes <- publishOutcome{err: err}
			return
		}
		confirmed, err := confirmation.WaitContext(ctx)
		outcomes <- publishOutcome{confirmed: confirmed, err: err}
	}()
	confirmed := false
	publishErr := error(nil)
	select {
	case outcome := <-outcomes:
		confirmed = outcome.confirmed
		publishErr = outcome.err
	case <-time.After(7 * time.Second):
		publishErr = context.DeadlineExceeded
	}
	blocked := false
	reason := ""
	select {
	case event := <-blockedEvents:
		blocked = event.Active
		reason = event.Reason
	default:
	}
	passed := false
	name := "publisher-flow.resumed"
	if expectBlocked {
		name = "publisher-flow.blocked"
		passed = alarmStatus != http.StatusOK && blocked && !confirmed
	} else {
		passed = alarmStatus == http.StatusOK && confirmed && publishErr == nil
	}
	observed := map[string]any{"alarm_http_status": alarmStatus, "blocked_event": blocked, "blocked_reason": reason, "publish_confirmed": confirmed}
	if publishErr != nil {
		observed["publish_error"] = publishErr.Error()
	}
	_ = conn.CloseDeadline(time.Now().Add(time.Second))
	r.Checks = append(r.Checks, check{Name: name, Passed: passed, Observed: observed})
	if !passed {
		return r, errors.New("publisher flow control invariant failed")
	}
	return r, nil
}

func waitAlarmState(endpoint string, alarmExpected bool, timeout time.Duration) (int, error) {
	client := &http.Client{Timeout: 5 * time.Second}
	deadline := time.Now().Add(timeout)
	last := 0
	for time.Now().Before(deadline) {
		req, _ := http.NewRequest(http.MethodGet, strings.TrimRight(endpoint, "/")+"/api/health/checks/alarms", nil)
		req.SetBasicAuth("atlas", "atlas-local-only")
		resp, err := client.Do(req)
		if err == nil {
			last = resp.StatusCode
			resp.Body.Close()
			if (alarmExpected && last != http.StatusOK) || (!alarmExpected && last == http.StatusOK) {
				return last, nil
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
	return last, fmt.Errorf("alarm state did not become expected=%v, last status=%d", alarmExpected, last)
}
