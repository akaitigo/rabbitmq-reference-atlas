package main

import (
	"errors"
	"testing"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

type testAcknowledger struct {
	err error
}

func (ack testAcknowledger) Ack(uint64, bool) error        { return ack.err }
func (ack testAcknowledger) Nack(uint64, bool, bool) error { return ack.err }
func (ack testAcknowledger) Reject(uint64, bool) error     { return ack.err }

type testGetResult struct {
	delivery amqp.Delivery
	ok       bool
	err      error
}

type testRecoverySession struct {
	results []testGetResult
	closed  bool
}

func (session *testRecoverySession) Get(string, bool) (amqp.Delivery, bool, error) {
	if len(session.results) == 0 {
		return amqp.Delivery{}, false, nil
	}
	result := session.results[0]
	session.results = session.results[1:]
	return result.delivery, result.ok, result.err
}

func (session *testRecoverySession) DeleteQueue(string) error { return nil }
func (session *testRecoverySession) Close()                   { session.closed = true }

func delivery(messageID string, ackError error) amqp.Delivery {
	return amqp.Delivery{Acknowledger: testAcknowledger{err: ackError}, DeliveryTag: 1, MessageId: messageID}
}

func TestRecoverMessagesReopensClosedChannelAfterLeaderElection(t *testing.T) {
	first := &testRecoverySession{results: []testGetResult{{err: errors.New("channel closed while quorum leader is unavailable")}}}
	second := &testRecoverySession{results: []testGetResult{
		{delivery: delivery("message-1", nil), ok: true},
		{delivery: delivery("message-2", nil), ok: true},
	}}
	sessions := []recoverySession{first, second}
	openCalls := 0
	opener := func(time.Duration) (recoverySession, error) {
		result := sessions[openCalls]
		openCalls++
		return result, nil
	}

	stats := recoverMessages(opener, "queue", 2, time.Second, 0)
	defer stats.currentSession.Close()

	if openCalls != 2 || stats.sessions != 2 || stats.getErrors != 1 {
		t.Fatalf("expected one reconnect after channel error, got opens=%d sessions=%d get_errors=%d", openCalls, stats.sessions, stats.getErrors)
	}
	if !first.closed {
		t.Fatal("failed recovery session was not closed")
	}
	if len(stats.messageIDs) != 2 || stats.messageIDs[0] != "message-1" || stats.messageIDs[1] != "message-2" {
		t.Fatalf("unexpected recovered message IDs: %#v", stats.messageIDs)
	}
}

func TestRecoverMessagesDoesNotCountFailedAcknowledgement(t *testing.T) {
	first := &testRecoverySession{results: []testGetResult{{delivery: delivery("message-1", errors.New("ack channel closed")), ok: true}}}
	second := &testRecoverySession{results: []testGetResult{{delivery: delivery("message-1", nil), ok: true}}}
	sessions := []recoverySession{first, second}
	openCalls := 0
	opener := func(time.Duration) (recoverySession, error) {
		result := sessions[openCalls]
		openCalls++
		return result, nil
	}

	stats := recoverMessages(opener, "queue", 1, time.Second, 0)
	defer stats.currentSession.Close()

	if stats.ackErrors != 1 || stats.deliveries != 1 || stats.duplicates != 0 || len(stats.messageIDs) != 1 {
		t.Fatalf("failed acknowledgement corrupted recovery accounting: %#v", stats)
	}
}
