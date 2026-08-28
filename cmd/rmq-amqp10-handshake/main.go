package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var (
	saslHeader       = []byte{'A', 'M', 'Q', 'P', 3, 1, 0, 0}
	bareAMQP10Header = []byte{'A', 'M', 'Q', 'P', 0, 1, 0, 0}
	amqp091Header    = []byte{'A', 'M', 'Q', 'P', 0, 0, 9, 1}
	unsupportedSASL  = []byte{'A', 'M', 'Q', 'P', 3, 2, 0, 0}
)

type observation struct {
	Endpoint    string `json:"endpoint"`
	SentHex     string `json:"sent_hex"`
	ReceivedHex string `json:"received_hex"`
	Passed      bool   `json:"passed"`
	Oracle      string `json:"oracle"`
}

type artifact struct {
	SchemaVersion int           `json:"schema_version"`
	BehaviorID    string        `json:"behavior_id"`
	Scenario      string        `json:"scenario"`
	CreatedAt     string        `json:"created_at"`
	Runtime       string        `json:"runtime"`
	Checks        []observation `json:"checks"`
	Passed        bool          `json:"passed"`
}

func exchangeHeader(endpoint string, sent []byte) ([]byte, error) {
	connection, err := net.DialTimeout("tcp", endpoint, 3*time.Second)
	if err != nil {
		return nil, err
	}
	defer connection.Close()
	if err := connection.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
		return nil, err
	}
	if _, err := connection.Write(sent); err != nil {
		return nil, err
	}
	received := make([]byte, 8)
	if _, err := io.ReadFull(connection, received); err != nil {
		return nil, err
	}
	return received, nil
}

func observe(endpoint string, sent []byte, oracle string, predicate func([]byte) bool) observation {
	received, err := exchangeHeader(endpoint, sent)
	item := observation{Endpoint: endpoint, SentHex: hex.EncodeToString(sent), Oracle: oracle}
	if err != nil {
		item.Oracle = fmt.Sprintf("%s: %v", oracle, err)
		return item
	}
	item.ReceivedHex = hex.EncodeToString(received)
	item.Passed = predicate(received)
	return item
}

func allPassed(checks []observation) bool {
	if len(checks) == 0 {
		return false
	}
	for _, check := range checks {
		if !check.Passed {
			return false
		}
	}
	return true
}

func writeArtifact(outputDir, scenario string, checks []observation) error {
	record := artifact{
		SchemaVersion: 1,
		BehaviorID:    "amqp10.version-negotiation",
		Scenario:      scenario,
		CreatedAt:     time.Now().UTC().Format(time.RFC3339Nano),
		Runtime:       "RabbitMQ 4.3.5 native AMQP 1.0 listener; three nodes",
		Checks:        checks,
		Passed:        allPassed(checks),
	}
	if !record.Passed {
		return fmt.Errorf("%s scenario oracle failed", scenario)
	}
	data, err := json.MarshalIndent(record, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(outputDir, fmt.Sprintf("definitive.amqp10.version-negotiation.%s.json", scenario))
	return os.WriteFile(path, append(data, '\n'), 0o644)
}

func run(endpoints []string, outputDir string) error {
	if len(endpoints) != 3 {
		return fmt.Errorf("three endpoints are required, got %d", len(endpoints))
	}
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return err
	}
	scenarios := map[string][]observation{
		"normal": {}, "boundary": {}, "rejection": {}, "security": {},
	}
	for _, endpoint := range endpoints {
		scenarios["normal"] = append(scenarios["normal"], observe(endpoint, saslHeader,
			"SASL protocol header is accepted and echoed", func(received []byte) bool { return bytes.Equal(received, saslHeader) }))
		scenarios["boundary"] = append(scenarios["boundary"], observe(endpoint, amqp091Header,
			"the shared listener accepts AMQP 0-9-1 and starts a method frame", func(received []byte) bool { return len(received) == 8 && received[0] == 1 }))
		scenarios["rejection"] = append(scenarios["rejection"], observe(endpoint, unsupportedSASL,
			"an unsupported SASL version is answered with the supported SASL header", func(received []byte) bool { return bytes.Equal(received, saslHeader) }))
		scenarios["security"] = append(scenarios["security"], observe(endpoint, bareAMQP10Header,
			"bare AMQP 1.0 is rejected by requiring SASL negotiation", func(received []byte) bool { return bytes.Equal(received, saslHeader) }))
	}
	for _, scenario := range []string{"normal", "boundary", "rejection", "security"} {
		if err := writeArtifact(outputDir, scenario, scenarios[scenario]); err != nil {
			return err
		}
	}
	return nil
}

func main() {
	endpointFlag := flag.String("endpoints", "127.0.0.1:25672,127.0.0.1:25673,127.0.0.1:25674", "comma-separated RabbitMQ endpoints")
	outputDir := flag.String("output-dir", "evidence/raw", "artifact output directory")
	flag.Parse()
	endpoints := strings.Split(*endpointFlag, ",")
	if err := run(endpoints, *outputDir); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
