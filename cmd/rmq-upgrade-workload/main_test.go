package main

import "testing"

func TestSanitizedEndpointsRemoveUserInfo(t *testing.T) {
	actual := sanitizedEndpoints([]string{"amqp://atlas:local-password@127.0.0.1:27672/"})
	if len(actual) != 1 || actual[0] != "amqp://127.0.0.1:27672/" {
		t.Fatalf("unexpected sanitized endpoint: %#v", actual)
	}
}
