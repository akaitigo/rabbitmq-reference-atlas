package main

import "testing"

func TestMatchesQueueStates(t *testing.T) {
	tests := []struct {
		name     string
		snapshot queueSnapshot
		expected expectedState
		want     bool
	}{
		{"ready", queueSnapshot{State: "running", Messages: 1, MessagesReady: 1}, expectedState{Messages: 1, MessagesReady: 1}, true},
		{"unacked", queueSnapshot{State: "running", Messages: 1, MessagesUnacknowledged: 1}, expectedState{Messages: 1, MessagesUnacknowledged: 1}, true},
		{"acked", queueSnapshot{State: "running"}, expectedState{}, true},
		{"wrong state", queueSnapshot{State: "down"}, expectedState{}, false},
		{"wrong count", queueSnapshot{State: "running", Messages: 1}, expectedState{}, false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := matches(test.snapshot, test.expected); got != test.want {
				t.Fatalf("matches()=%v want=%v", got, test.want)
			}
		})
	}
}

func TestSafeEndpointRemovesCredentials(t *testing.T) {
	got := safeEndpoint("http://atlas:secret@127.0.0.1:35672")
	if got != "http://127.0.0.1:35672" {
		t.Fatalf("unexpected safe endpoint: %s", got)
	}
}
