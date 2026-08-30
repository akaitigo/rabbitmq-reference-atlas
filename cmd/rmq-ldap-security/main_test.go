package main

import (
	"strings"
	"testing"
)

func TestRedactRemovesAllPasswords(t *testing.T) {
	secrets := credentials{
		allowedPassword: "allowed-secret",
		deniedPassword:  "denied-secret",
		badPassword:     "bad-secret",
	}
	actual := redact("allowed-secret denied-secret bad-secret", secrets)
	for _, secret := range []string{secrets.allowedPassword, secrets.deniedPassword, secrets.badPassword} {
		if strings.Contains(actual, secret) {
			t.Fatalf("redacted text contains secret %q", secret)
		}
	}
}

func TestRunRejectsNonThreeNodeProfile(t *testing.T) {
	_, err := run("authentication", []string{"127.0.0.1:26672"}, credentials{})
	if err == nil || !strings.Contains(err.Error(), "three endpoints") {
		t.Fatalf("expected three endpoint error, got %v", err)
	}
}

func TestRunRejectsUnknownMode(t *testing.T) {
	_, err := run("unknown", []string{"one", "two", "three"}, credentials{})
	if err == nil || !strings.Contains(err.Error(), "unsupported mode") {
		t.Fatalf("expected unsupported mode error, got %v", err)
	}
}
