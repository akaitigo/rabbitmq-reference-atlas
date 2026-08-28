package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestCredentialedEndpoints(t *testing.T) {
	got, err := credentialedEndpoints([]string{"amqp://127.0.0.1:25672/"}, "limited user", "secret/value", "isolated-vhost")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || !strings.Contains(got[0], "limited%20user:secret%2Fvalue@") || !strings.HasSuffix(got[0], "/isolated-vhost") {
		t.Fatalf("unexpected credentialed endpoint: %v", got)
	}
}

func TestAPIClientAndSafeEndpoint(t *testing.T) {
	transport := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		username, password, ok := r.BasicAuth()
		if !ok || username != "atlas" || password != "secret" {
			t.Fatalf("unexpected basic auth: username=%q password=%q", username, password)
		}
		if r.URL.Path != "/api/overview" {
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
		body, err := json.Marshal(map[string]any{"rabbitmq_version": "4.3.5"})
		if err != nil {
			t.Fatal(err)
		}
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(bytes.NewReader(body)), Header: make(http.Header)}, nil
	})

	client := newAPIClient("http://management.example", "atlas", "secret")
	client.http.Transport = transport
	var result map[string]any
	status, err := client.request(http.MethodGet, "/api/overview", nil, &result)
	if err != nil || status != http.StatusOK || result["rabbitmq_version"] != "4.3.5" {
		t.Fatalf("unexpected response: status=%d result=%v error=%v", status, result, err)
	}
	if got := safeEndpoint("http://atlas:secret@localhost:15672"); strings.Contains(got, "secret") || strings.Contains(got, "atlas@") {
		t.Fatalf("credentials leaked from safe endpoint: %s", got)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) { return f(request) }

func TestFinalizeMakesEveryFailedCheckFatal(t *testing.T) {
	r := finalize(report{Checks: []check{
		passed("positive", "security", nil),
		failed("negative", "operations", errFixture("failure")),
	}})
	if r.Passed || r.Summary.Passed != 1 || r.Summary.Failed != 1 {
		t.Fatalf("unexpected summary: %+v", r)
	}
}

type errFixture string

func (e errFixture) Error() string { return string(e) }
