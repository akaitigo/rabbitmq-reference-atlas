package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const targetVersion = "4.3.5"

type check struct {
	Name     string         `json:"name"`
	Passed   bool           `json:"passed"`
	Observed map[string]any `json:"observed,omitempty"`
	Error    string         `json:"error,omitempty"`
}

type queueSnapshot struct {
	Phase                  string `json:"phase"`
	SampledAt              string `json:"sampled_at"`
	Name                   string `json:"name"`
	Vhost                  string `json:"vhost"`
	Node                   string `json:"node"`
	State                  string `json:"state"`
	Messages               int    `json:"messages"`
	MessagesReady          int    `json:"messages_ready"`
	MessagesUnacknowledged int    `json:"messages_unacknowledged"`
}

type report struct {
	SchemaVersion int             `json:"schema_version"`
	Mode          string          `json:"mode"`
	RunID         string          `json:"run_id"`
	CreatedAt     string          `json:"created_at"`
	RabbitMQ      string          `json:"rabbitmq_version"`
	Queue         string          `json:"queue"`
	Checks        []check         `json:"checks"`
	Transitions   []queueSnapshot `json:"transitions"`
	Passed        bool            `json:"passed"`
	Summary       summary         `json:"summary"`
}

type summary struct {
	Passed int `json:"passed"`
	Failed int `json:"failed"`
}

type expectedState struct {
	Messages               int
	MessagesReady          int
	MessagesUnacknowledged int
}

type managementClient struct {
	base     string
	username string
	password string
	http     *http.Client
}

func main() {
	amqpURL := flag.String("amqp-url", "amqp://atlas:atlas-local-only@127.0.0.1:25672/", "AMQP endpoint")
	managementURL := flag.String("management-url", "http://127.0.0.1:35672", "Management API endpoint")
	managementUser := flag.String("management-user", "atlas", "Management API user")
	managementPassword := flag.String("management-password", "atlas-local-only", "Management API password")
	output := flag.String("output", "", "JSON output path (stdout when empty)")
	flag.Parse()

	r := run(*amqpURL, *managementURL, *managementUser, *managementPassword)
	if err := writeReport(*output, r); err != nil {
		fmt.Fprintf(os.Stderr, "状態遷移JSONを書き込めません: %v\n", err)
		os.Exit(2)
	}
	if !r.Passed {
		os.Exit(1)
	}
}

func run(amqpURL, managementURL, managementUser, managementPassword string) report {
	runID := fmt.Sprintf("%d", time.Now().UTC().UnixNano())
	queue := "atlas.observability." + runID
	r := report{
		SchemaVersion: 1,
		Mode:          "observability-state-transition",
		RunID:         runID,
		CreatedAt:     time.Now().UTC().Format(time.RFC3339Nano),
		RabbitMQ:      targetVersion,
		Queue:         queue,
	}
	manager := newManagementClient(managementURL, managementUser, managementPassword)

	actualVersion, err := manager.waitVersion(30 * time.Second)
	versionObserved := map[string]any{
		"endpoint": safeEndpoint(managementURL), "expected": targetVersion, "observed": actualVersion,
	}
	if err != nil || actualVersion != targetVersion {
		r.Checks = append(r.Checks, failObserved("environment.rabbitmq-version", versionObserved, combineError(err, fmt.Sprintf("RabbitMQ %sを期待しました", targetVersion))))
		return finalize(r)
	}
	r.Checks = append(r.Checks, pass("environment.rabbitmq-version", versionObserved))

	conn, err := amqp.DialConfig(amqpURL, amqp.Config{Heartbeat: 5 * time.Second, Dial: amqp.DefaultDial(5 * time.Second)})
	if err != nil {
		r.Checks = append(r.Checks, fail("observability.amqp-connect", err))
		return finalize(r)
	}
	defer conn.Close()

	ch, err := conn.Channel()
	if err != nil {
		r.Checks = append(r.Checks, fail("observability.channel", err))
		return finalize(r)
	}
	defer ch.Close()

	created := false
	if _, err := ch.QueueDeclare(queue, true, false, false, false, nil); err != nil {
		r.Checks = append(r.Checks, fail("observability.queue-setup", err))
		return finalize(r)
	}
	created = true
	r.Checks = append(r.Checks, pass("observability.queue-setup", map[string]any{"queue": queue, "durable": true, "vhost": "/"}))

	if err := ch.Confirm(false); err != nil {
		r.Checks = append(r.Checks, fail("observability.publish-confirm", err))
	} else {
		confirmation, publishErr := ch.PublishWithDeferredConfirmWithContext(context.Background(), "", queue, false, false, amqp.Publishing{
			DeliveryMode: amqp.Persistent,
			MessageId:    "observability-" + runID,
			Body:         []byte("queue-state-transition"),
		})
		confirmed := publishErr == nil && confirmation != nil && confirmation.Wait() && confirmation.Acked()
		observed := map[string]any{"message_id": "observability-" + runID, "publisher_confirm": confirmed}
		if !confirmed {
			r.Checks = append(r.Checks, failObserved("observability.publish-confirm", observed, combineError(publishErr, "Publisher Confirmがackではありません")))
		} else {
			r.Checks = append(r.Checks, pass("observability.publish-confirm", observed))
			ready, pollErr := manager.waitQueue("/", queue, "ready", expectedState{Messages: 1, MessagesReady: 1, MessagesUnacknowledged: 0}, 20*time.Second)
			r.Transitions = append(r.Transitions, ready)
			if pollErr != nil {
				r.Checks = append(r.Checks, failObserved("observability.state-ready", snapshotObserved(ready), pollErr))
			} else {
				r.Checks = append(r.Checks, pass("observability.state-ready", snapshotObserved(ready)))
				observeUnacked(ch, manager, queue, &r)
			}
		}
	}

	if created {
		deleted, deleteErr := ch.QueueDelete(queue, false, false, false)
		status, absentErr := manager.waitQueueAbsent("/", queue, 5*time.Second)
		observed := map[string]any{"queue": queue, "deleted_messages": deleted, "management_get_after_delete": status}
		if deleteErr != nil || absentErr != nil || status != http.StatusNotFound {
			r.Checks = append(r.Checks, failObserved("operations.queue-cleanup", observed, combineError(firstError(deleteErr, absentErr), "Queue削除後のManagement API 404を確認できません")))
		} else {
			r.Checks = append(r.Checks, pass("operations.queue-cleanup", observed))
		}
	}
	return finalize(r)
}

func observeUnacked(ch *amqp.Channel, manager *managementClient, queue string, r *report) {
	if err := ch.Qos(1, 0, false); err != nil {
		r.Checks = append(r.Checks, fail("observability.consumer-qos", err))
		return
	}
	deliveries, err := ch.Consume(queue, "atlas-observability", false, false, false, false, nil)
	if err != nil {
		r.Checks = append(r.Checks, fail("observability.consumer", err))
		return
	}
	var delivery amqp.Delivery
	select {
	case received, ok := <-deliveries:
		if !ok {
			r.Checks = append(r.Checks, fail("observability.consumer", errors.New("Delivery channelが閉じました")))
			return
		}
		delivery = received
	case <-time.After(5 * time.Second):
		r.Checks = append(r.Checks, fail("observability.consumer", errors.New("Delivery取得がtimeoutしました")))
		return
	}

	unacked, err := manager.waitQueue("/", queue, "unacked", expectedState{Messages: 1, MessagesReady: 0, MessagesUnacknowledged: 1}, 20*time.Second)
	r.Transitions = append(r.Transitions, unacked)
	if err != nil {
		r.Checks = append(r.Checks, failObserved("observability.state-unacked", snapshotObserved(unacked), err))
		_ = delivery.Nack(false, false)
		return
	}
	r.Checks = append(r.Checks, pass("observability.state-unacked", snapshotObserved(unacked)))

	if err := delivery.Ack(false); err != nil {
		r.Checks = append(r.Checks, fail("observability.ack", err))
		return
	}
	acked, err := manager.waitQueue("/", queue, "acked", expectedState{Messages: 0, MessagesReady: 0, MessagesUnacknowledged: 0}, 20*time.Second)
	r.Transitions = append(r.Transitions, acked)
	if err != nil {
		r.Checks = append(r.Checks, failObserved("observability.state-acked", snapshotObserved(acked), err))
		return
	}
	r.Checks = append(r.Checks, pass("observability.state-acked", snapshotObserved(acked)))
}

func newManagementClient(endpoint, username, password string) *managementClient {
	return &managementClient{
		base: strings.TrimRight(endpoint, "/"), username: username, password: password,
		http: &http.Client{Timeout: 10 * time.Second},
	}
}

func (c *managementClient) version() (string, error) {
	var overview struct {
		RabbitMQVersion string `json:"rabbitmq_version"`
	}
	status, err := c.request(http.MethodGet, "/api/overview", &overview)
	if err != nil {
		return "", err
	}
	if status != http.StatusOK {
		return "", fmt.Errorf("Management API overview: status=%d", status)
	}
	return overview.RabbitMQVersion, nil
}

func (c *managementClient) waitVersion(timeout time.Duration) (string, error) {
	deadline := time.Now().Add(timeout)
	var actual string
	var lastErr error
	for time.Now().Before(deadline) {
		actual, lastErr = c.version()
		if lastErr == nil {
			return actual, nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	return actual, lastErr
}

func (c *managementClient) waitQueue(vhost, queue, phase string, expected expectedState, timeout time.Duration) (queueSnapshot, error) {
	deadline := time.Now().Add(timeout)
	var last queueSnapshot
	var lastErr error
	for time.Now().Before(deadline) {
		status, current, err := c.queue(vhost, queue)
		lastErr = err
		if err == nil && status == http.StatusOK {
			current.Phase = phase
			current.SampledAt = time.Now().UTC().Format(time.RFC3339Nano)
			last = current
			if matches(current, expected) {
				return current, nil
			}
		}
		time.Sleep(250 * time.Millisecond)
	}
	if lastErr != nil {
		return last, fmt.Errorf("%s状態のManagement API取得に失敗しました: %w", phase, lastErr)
	}
	return last, fmt.Errorf("%s状態がtimeoutしました: expected messages=%d ready=%d unacked=%d; observed messages=%d ready=%d unacked=%d", phase,
		expected.Messages, expected.MessagesReady, expected.MessagesUnacknowledged, last.Messages, last.MessagesReady, last.MessagesUnacknowledged)
}

func (c *managementClient) queue(vhost, queue string) (int, queueSnapshot, error) {
	var snapshot queueSnapshot
	status, err := c.request(http.MethodGet, "/api/queues/"+url.PathEscape(vhost)+"/"+url.PathEscape(queue), &snapshot)
	return status, snapshot, err
}

func (c *managementClient) queueStatus(vhost, queue string) (int, error) {
	status, _, err := c.queue(vhost, queue)
	return status, err
}

func (c *managementClient) waitQueueAbsent(vhost, queue string, timeout time.Duration) (int, error) {
	deadline := time.Now().Add(timeout)
	var status int
	var lastErr error
	for time.Now().Before(deadline) {
		status, lastErr = c.queueStatus(vhost, queue)
		if lastErr == nil && status == http.StatusNotFound {
			return status, nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return status, lastErr
}

func (c *managementClient) request(method, path string, destination any) (int, error) {
	req, err := http.NewRequest(method, c.base+path, nil)
	if err != nil {
		return 0, err
	}
	req.SetBasicAuth(c.username, c.password)
	resp, err := c.http.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return resp.StatusCode, err
	}
	if resp.StatusCode == http.StatusNotFound {
		return resp.StatusCode, nil
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return resp.StatusCode, fmt.Errorf("Management API %s %s: %s", method, path, strings.TrimSpace(string(data)))
	}
	if destination != nil && len(data) > 0 {
		if err := json.NewDecoder(bytes.NewReader(data)).Decode(destination); err != nil {
			return resp.StatusCode, err
		}
	}
	return resp.StatusCode, nil
}

func matches(snapshot queueSnapshot, expected expectedState) bool {
	return snapshot.Messages == expected.Messages &&
		snapshot.MessagesReady == expected.MessagesReady &&
		snapshot.MessagesUnacknowledged == expected.MessagesUnacknowledged &&
		snapshot.State == "running"
}

func snapshotObserved(snapshot queueSnapshot) map[string]any {
	return map[string]any{
		"phase": snapshot.Phase, "sampled_at": snapshot.SampledAt, "queue": snapshot.Name,
		"node": snapshot.Node, "state": snapshot.State, "messages": snapshot.Messages,
		"messages_ready": snapshot.MessagesReady, "messages_unacknowledged": snapshot.MessagesUnacknowledged,
	}
}

func safeEndpoint(value string) string {
	u, err := url.Parse(value)
	if err != nil {
		return value
	}
	u.User = nil
	return u.String()
}

func pass(name string, observed map[string]any) check {
	return check{Name: name, Passed: true, Observed: observed}
}

func fail(name string, err error) check { return failObserved(name, nil, err) }

func failObserved(name string, observed map[string]any, err error) check {
	message := ""
	if err != nil {
		message = err.Error()
	}
	return check{Name: name, Passed: false, Observed: observed, Error: message}
}

func combineError(err error, fallback string) error {
	if err != nil {
		return fmt.Errorf("%s: %w", fallback, err)
	}
	return errors.New(fallback)
}

func firstError(values ...error) error {
	for _, err := range values {
		if err != nil {
			return err
		}
	}
	return nil
}

func finalize(r report) report {
	r.Passed = len(r.Checks) > 0
	for _, item := range r.Checks {
		if item.Passed {
			r.Summary.Passed++
		} else {
			r.Summary.Failed++
			r.Passed = false
		}
	}
	return r
}

func writeReport(output string, r report) error {
	data, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if output == "" {
		_, err = os.Stdout.Write(data)
		return err
	}
	return os.WriteFile(output, data, 0o644)
}
