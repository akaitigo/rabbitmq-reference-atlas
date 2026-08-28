package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"net/url"
	"os"
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

type report struct {
	SchemaVersion int      `json:"schema_version"`
	Mode          string   `json:"mode"`
	RunID         string   `json:"run_id"`
	CreatedAt     string   `json:"created_at"`
	Checks        []check  `json:"checks"`
	Queue         string   `json:"queue,omitempty"`
	Leader        string   `json:"leader,omitempty"`
	MessageIDs    []string `json:"message_ids,omitempty"`
	Nodes         []node   `json:"nodes,omitempty"`
}

type node struct {
	Name    string `json:"name"`
	Running bool   `json:"running"`
}

type queueInfo struct {
	Name          string   `json:"name"`
	Node          string   `json:"node"`
	Leader        string   `json:"leader"`
	Members       []string `json:"members"`
	OnlineMembers []string `json:"online"`
}

func main() {
	mode := flag.String("mode", "core", "core, prepare-failure, verify-recovery, cluster")
	amqpURLs := flag.String("amqp-urls", "amqp://atlas:atlas-local-only@127.0.0.1:25672/", "comma-separated AMQP endpoints")
	managementURLs := flag.String("management-urls", "http://127.0.0.1:35672", "comma-separated Management endpoints")
	queue := flag.String("queue", "", "existing queue for recovery verification")
	expected := flag.Int("expected", 5, "expected message count")
	output := flag.String("output", "", "JSON output path")
	flag.Parse()

	runID := fmt.Sprintf("%d", time.Now().UTC().UnixNano())
	var r report
	var err error
	switch *mode {
	case "core":
		r, err = runCore(split(*amqpURLs), runID)
	case "prepare-failure":
		r, err = prepareFailure(split(*amqpURLs), split(*managementURLs), runID, *expected)
	case "verify-recovery":
		r, err = verifyRecovery(split(*amqpURLs), runID, *queue, *expected)
	case "cluster":
		r, err = inspectCluster(split(*managementURLs), runID)
	default:
		err = fmt.Errorf("unknown mode %q", *mode)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "lab failed: %v\n", err)
		os.Exit(1)
	}
	if err := writeReport(*output, r); err != nil {
		fmt.Fprintf(os.Stderr, "write report: %v\n", err)
		os.Exit(1)
	}
}

func split(value string) []string {
	var out []string
	for _, item := range strings.Split(value, ",") {
		if item = strings.TrimSpace(item); item != "" {
			out = append(out, item)
		}
	}
	return out
}

func connect(endpoints []string, deadline time.Duration) (*amqp.Connection, error) {
	end := time.Now().Add(deadline)
	var last error
	for time.Now().Before(end) {
		for _, endpoint := range endpoints {
			conn, err := amqp.DialConfig(endpoint, amqp.Config{Heartbeat: 5 * time.Second, Dial: amqp.DefaultDial(3 * time.Second)})
			if err == nil {
				return conn, nil
			}
			last = err
		}
		time.Sleep(time.Second)
	}
	return nil, fmt.Errorf("no AMQP endpoint became ready: %w", last)
}

func runCore(endpoints []string, runID string) (report, error) {
	conn, err := connect(endpoints, 30*time.Second)
	if err != nil {
		return report{}, err
	}
	defer conn.Close()
	checks := make([]check, 0, 5)
	for _, lab := range []func(*amqp.Connection, string) (check, error){exchangeQueue, ackRedelivery, deadLetter, consumerFlowControl} {
		result, err := lab(conn, "atlas."+runID)
		if err != nil {
			return report{}, err
		}
		checks = append(checks, result)
	}
	return report{SchemaVersion: 1, Mode: "core", RunID: runID, CreatedAt: time.Now().UTC().Format(time.RFC3339), Checks: checks}, nil
}

func exchangeQueue(conn *amqp.Connection, prefix string) (check, error) {
	ch, err := conn.Channel()
	if err != nil {
		return check{}, err
	}
	defer ch.Close()
	exchange, queueName, key := prefix+".direct", prefix+".exchange-queue", "orders.created"
	if err := ch.ExchangeDeclare(exchange, "direct", true, false, false, false, nil); err != nil {
		return check{}, err
	}
	defer ch.ExchangeDelete(exchange, false, false)
	q, err := ch.QueueDeclare(queueName, true, false, false, false, nil)
	if err != nil {
		return check{}, err
	}
	defer ch.QueueDelete(q.Name, false, false, false)
	if err := ch.QueueBind(q.Name, key, exchange, false, nil); err != nil {
		return check{}, err
	}
	messageID := prefix + ".exchange-message"
	if err := publishConfirmed(ch, exchange, key, messageID); err != nil {
		return check{}, err
	}
	d, ok, err := getEventually(ch, q.Name, 5*time.Second)
	if err != nil || !ok {
		return check{}, fmt.Errorf("exchange delivery missing: %w", err)
	}
	if err := d.Ack(false); err != nil {
		return check{}, err
	}
	inspected, err := ch.QueueInspect(q.Name)
	if err != nil {
		return check{}, err
	}
	passed := d.MessageId == messageID && inspected.Messages == 0
	return check{Name: "exchange-queue-confirm", Passed: passed, Observed: map[string]any{"exchange": exchange, "queue": q.Name, "routing_key": key, "message_id": d.MessageId, "publisher_confirm": "ack", "messages_after_ack": inspected.Messages}}, assert(passed, "exchange/queue invariant failed")
}

func ackRedelivery(conn *amqp.Connection, prefix string) (check, error) {
	ch, err := conn.Channel()
	if err != nil {
		return check{}, err
	}
	defer ch.Close()
	q, err := ch.QueueDeclare(prefix+".ack-redelivery", true, false, false, false, nil)
	if err != nil {
		return check{}, err
	}
	defer ch.QueueDelete(q.Name, false, false, false)
	messageID := prefix + ".redelivery-message"
	if err := publishConfirmed(ch, "", q.Name, messageID); err != nil {
		return check{}, err
	}
	first, ok, err := getEventually(ch, q.Name, 5*time.Second)
	if err != nil || !ok {
		return check{}, fmt.Errorf("first delivery missing: %w", err)
	}
	firstFlag := first.Redelivered
	if err := first.Nack(false, true); err != nil {
		return check{}, err
	}
	second, ok, err := getEventually(ch, q.Name, 5*time.Second)
	if err != nil || !ok {
		return check{}, fmt.Errorf("redelivery missing: %w", err)
	}
	if err := second.Ack(false); err != nil {
		return check{}, err
	}
	inspected, err := ch.QueueInspect(q.Name)
	if err != nil {
		return check{}, err
	}
	passed := !firstFlag && second.Redelivered && second.MessageId == messageID && inspected.Messages == 0
	return check{Name: "ack-redelivery", Passed: passed, Observed: map[string]any{"message_id": messageID, "first_redelivered": firstFlag, "second_redelivered": second.Redelivered, "messages_after_ack": inspected.Messages}}, assert(passed, "ack/redelivery invariant failed")
}

func deadLetter(conn *amqp.Connection, prefix string) (check, error) {
	ch, err := conn.Channel()
	if err != nil {
		return check{}, err
	}
	defer ch.Close()
	dlx, sourceName, deadName := prefix+".dlx", prefix+".source", prefix+".dead"
	if err := ch.ExchangeDeclare(dlx, "direct", true, false, false, false, nil); err != nil {
		return check{}, err
	}
	defer ch.ExchangeDelete(dlx, false, false)
	dead, err := ch.QueueDeclare(deadName, true, false, false, false, nil)
	if err != nil {
		return check{}, err
	}
	defer ch.QueueDelete(dead.Name, false, false, false)
	if err := ch.QueueBind(dead.Name, "dead", dlx, false, nil); err != nil {
		return check{}, err
	}
	source, err := ch.QueueDeclare(sourceName, true, false, false, false, amqp.Table{"x-dead-letter-exchange": dlx, "x-dead-letter-routing-key": "dead"})
	if err != nil {
		return check{}, err
	}
	defer ch.QueueDelete(source.Name, false, false, false)
	messageID := prefix + ".dead-message"
	if err := publishConfirmed(ch, "", source.Name, messageID); err != nil {
		return check{}, err
	}
	d, ok, err := getEventually(ch, source.Name, 5*time.Second)
	if err != nil || !ok {
		return check{}, fmt.Errorf("source delivery missing: %w", err)
	}
	if err := d.Reject(false); err != nil {
		return check{}, err
	}
	deadDelivery, ok, err := getEventually(ch, dead.Name, 8*time.Second)
	if err != nil || !ok {
		return check{}, fmt.Errorf("dead letter missing: %w", err)
	}
	_, hasDeath := deadDelivery.Headers["x-death"]
	if err := deadDelivery.Ack(false); err != nil {
		return check{}, err
	}
	passed := hasDeath && deadDelivery.MessageId == messageID
	return check{Name: "dead-letter", Passed: passed, Observed: map[string]any{"message_id": messageID, "dead_letter_message_id": deadDelivery.MessageId, "x_death_present": hasDeath}}, assert(passed, "dead-letter invariant failed")
}

func consumerFlowControl(conn *amqp.Connection, prefix string) (check, error) {
	ch, err := conn.Channel()
	if err != nil {
		return check{}, err
	}
	defer ch.Close()
	q, err := ch.QueueDeclare(prefix+".prefetch", true, false, false, false, nil)
	if err != nil {
		return check{}, err
	}
	defer ch.QueueDelete(q.Name, false, false, false)
	if err := ch.Qos(1, 0, false); err != nil {
		return check{}, err
	}
	for i := 1; i <= 2; i++ {
		if err := publishConfirmed(ch, "", q.Name, fmt.Sprintf("%s.prefetch-%d", prefix, i)); err != nil {
			return check{}, err
		}
	}
	deliveries, err := ch.Consume(q.Name, "atlas-prefetch", false, false, false, false, nil)
	if err != nil {
		return check{}, err
	}
	first := <-deliveries
	secondBeforeAck := false
	select {
	case <-deliveries:
		secondBeforeAck = true
	case <-time.After(500 * time.Millisecond):
	}
	if err := first.Ack(false); err != nil {
		return check{}, err
	}
	secondAfterAck := false
	var second amqp.Delivery
	select {
	case second = <-deliveries:
		secondAfterAck = true
	case <-time.After(5 * time.Second):
	}
	if secondAfterAck {
		_ = second.Ack(false)
	}
	_ = ch.Cancel("atlas-prefetch", false)
	passed := !secondBeforeAck && secondAfterAck
	return check{Name: "consumer-prefetch", Passed: passed, Observed: map[string]any{"prefetch": 1, "second_before_ack": secondBeforeAck, "second_after_ack": secondAfterAck}}, assert(passed, "consumer prefetch invariant failed")
}

func publishConfirmed(ch *amqp.Channel, exchange, key, messageID string) error {
	if err := ch.Confirm(false); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	confirmation, err := ch.PublishWithDeferredConfirmWithContext(ctx, exchange, key, false, false, amqp.Publishing{DeliveryMode: amqp.Persistent, ContentType: "application/json", MessageId: messageID, Body: []byte(`{"atlas":"rabbitmq-reference-atlas"}`)})
	if err != nil {
		return err
	}
	if confirmation == nil {
		return errors.New("publisher confirm was not registered")
	}
	ack, err := confirmation.WaitContext(ctx)
	if err != nil {
		return fmt.Errorf("publisher confirm wait: %w", err)
	}
	if !ack {
		return errors.New("publisher confirm nack")
	}
	return nil
}

func getEventually(ch *amqp.Channel, queue string, timeout time.Duration) (amqp.Delivery, bool, error) {
	end := time.Now().Add(timeout)
	for time.Now().Before(end) {
		d, ok, err := ch.Get(queue, false)
		if err != nil {
			return amqp.Delivery{}, false, err
		}
		if ok {
			return d, true, nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return amqp.Delivery{}, false, nil
}

func prepareFailure(endpoints, management []string, runID string, count int) (report, error) {
	conn, err := connect(endpoints, 45*time.Second)
	if err != nil {
		return report{}, err
	}
	defer conn.Close()
	ch, err := conn.Channel()
	if err != nil {
		return report{}, err
	}
	defer ch.Close()
	queueName := "atlas." + runID + ".quorum-recovery"
	q, err := ch.QueueDeclare(queueName, true, false, false, false, amqp.Table{"x-queue-type": "quorum", "x-quorum-initial-group-size": int32(3)})
	if err != nil {
		return report{}, err
	}
	ids := make([]string, 0, count)
	for i := 1; i <= count; i++ {
		id := fmt.Sprintf("%s.message-%02d", runID, i)
		if err := publishConfirmed(ch, "", q.Name, id); err != nil {
			return report{}, err
		}
		ids = append(ids, id)
	}
	var info queueInfo
	for i := 0; i < 30; i++ {
		info, err = fetchQueue(management, q.Name)
		if err == nil && (info.Leader != "" || info.Node != "") && len(info.OnlineMembers) >= 3 {
			break
		}
		time.Sleep(time.Second)
	}
	if err != nil {
		return report{}, err
	}
	leader := info.Leader
	if leader == "" {
		leader = info.Node
	}
	passed := leader != "" && len(info.OnlineMembers) >= 3
	return report{SchemaVersion: 1, Mode: "prepare-failure", RunID: runID, CreatedAt: time.Now().UTC().Format(time.RFC3339), Queue: q.Name, Leader: leader, MessageIDs: ids, Checks: []check{{Name: "quorum-queue-prepared", Passed: passed, Observed: map[string]any{"leader": leader, "members": info.Members, "online_members": info.OnlineMembers, "confirmed_messages": len(ids)}}}}, assert(passed, "quorum queue did not reach three online members")
}

func verifyRecovery(endpoints []string, runID, queueName string, expected int) (report, error) {
	if queueName == "" {
		return report{}, errors.New("queue is required")
	}
	conn, err := connect(endpoints, 60*time.Second)
	if err != nil {
		return report{}, err
	}
	defer conn.Close()
	ch, err := conn.Channel()
	if err != nil {
		return report{}, err
	}
	defer ch.Close()
	ids := make([]string, 0, expected)
	deadline := time.Now().Add(60 * time.Second)
	for len(ids) < expected && time.Now().Before(deadline) {
		d, ok, err := ch.Get(queueName, false)
		if err != nil {
			time.Sleep(time.Second)
			continue
		}
		if !ok {
			time.Sleep(200 * time.Millisecond)
			continue
		}
		ids = append(ids, d.MessageId)
		if err := d.Ack(false); err != nil {
			return report{}, err
		}
	}
	sorted := append([]string(nil), ids...)
	sort.Strings(sorted)
	unique := map[string]bool{}
	for _, id := range ids {
		unique[id] = true
	}
	passed := len(ids) == expected && len(unique) == expected
	if _, err := ch.QueueDelete(queueName, false, false, false); err != nil {
		return report{}, err
	}
	return report{SchemaVersion: 1, Mode: "verify-recovery", RunID: runID, CreatedAt: time.Now().UTC().Format(time.RFC3339), Queue: queueName, MessageIDs: sorted, Checks: []check{{Name: "leader-failure-delivery", Passed: passed, Observed: map[string]any{"expected": expected, "received": len(ids), "unique": len(unique)}}}}, assert(passed, "recovery message invariant failed")
}

func inspectCluster(management []string, runID string) (report, error) {
	var nodes []node
	var err error
	for i := 0; i < 60; i++ {
		nodes, err = fetchNodes(management)
		if err == nil && runningCount(nodes) == 3 {
			break
		}
		time.Sleep(time.Second)
	}
	if err != nil {
		return report{}, err
	}
	passed := len(nodes) == 3 && runningCount(nodes) == 3
	return report{SchemaVersion: 1, Mode: "cluster", RunID: runID, CreatedAt: time.Now().UTC().Format(time.RFC3339), Nodes: nodes, Checks: []check{{Name: "three-running-members", Passed: passed, Observed: map[string]any{"nodes": len(nodes), "running": runningCount(nodes)}}}}, assert(passed, "cluster does not have three running members")
}

func fetchNodes(endpoints []string) ([]node, error) {
	var result []node
	err := managementGet(endpoints, "/api/nodes", &result)
	return result, err
}

func fetchQueue(endpoints []string, name string) (queueInfo, error) {
	var result queueInfo
	err := managementGet(endpoints, "/api/queues/%2F/"+url.PathEscape(name), &result)
	return result, err
}

func managementGet(endpoints []string, path string, target any) error {
	client := &http.Client{Timeout: 5 * time.Second}
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
		if resp.StatusCode != http.StatusOK {
			last = fmt.Errorf("management status %s", resp.Status)
			resp.Body.Close()
			continue
		}
		err = json.NewDecoder(resp.Body).Decode(target)
		resp.Body.Close()
		if err == nil {
			return nil
		}
		last = err
	}
	return last
}

func runningCount(nodes []node) int {
	count := 0
	for _, n := range nodes {
		if n.Running {
			count++
		}
	}
	return count
}

func assert(ok bool, message string) error {
	if !ok {
		return errors.New(message)
	}
	return nil
}

func writeReport(path string, value report) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if path == "" {
		_, err = os.Stdout.Write(data)
		return err
	}
	return os.WriteFile(path, data, 0o644)
}
