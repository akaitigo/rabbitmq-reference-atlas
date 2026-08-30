package main

import (
	"strings"
	"testing"
)

func TestRedact(t *testing.T) {
	secrets := credentials{user: "atlas", password: "allowed-secret", badPassword: "bad-secret"}
	actual := redact("allowed-secret and bad-secret", secrets)
	if strings.Contains(actual, "secret") || actual != "[redacted] and [redacted]" {
		t.Fatalf("credentials were not redacted: %q", actual)
	}
}

func TestLoadCredentialsFailsClosed(t *testing.T) {
	for _, key := range []string{allowedUserEnv, allowedPasswordEnv, badPasswordEnv} {
		t.Setenv(key, "")
	}
	if _, err := loadCredentials(); err == nil {
		t.Fatal("missing credentials must fail closed")
	}
}
