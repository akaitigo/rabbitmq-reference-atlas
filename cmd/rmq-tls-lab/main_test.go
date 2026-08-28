package main

import (
	"strings"
	"testing"
)

func TestURIsEscapeCredentialsAndUseExpectedSchemes(t *testing.T) {
	if got := secureURI("127.0.0.1:45671"); !strings.HasPrefix(got, "amqps://") || !strings.Contains(got, "atlas-local-only") {
		t.Fatalf("unexpected secure URI: %s", got)
	}
	if got := plainURI("127.0.0.1:45672"); !strings.HasPrefix(got, "amqp://") {
		t.Fatalf("unexpected plaintext URI: %s", got)
	}
}

func TestRejectedCheck(t *testing.T) {
	if rejectedCheck("negative", nil).Passed {
		t.Fatal("nil error must not prove rejection")
	}
	if !rejectedCheck("negative", errorsFixture("rejected")).Passed {
		t.Fatal("handshake error must prove rejection")
	}
}

type errorsFixture string

func (e errorsFixture) Error() string { return string(e) }
