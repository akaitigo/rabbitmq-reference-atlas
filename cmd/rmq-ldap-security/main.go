package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	allowedUserEnv     = "RABBITMQ_LDAP_ALLOWED_USER"
	allowedPasswordEnv = "RABBITMQ_LDAP_ALLOWED_PASSWORD"
	deniedUserEnv      = "RABBITMQ_LDAP_DENIED_USER"
	deniedPasswordEnv  = "RABBITMQ_LDAP_DENIED_PASSWORD"
	badPasswordEnv     = "RABBITMQ_LDAP_BAD_PASSWORD"
)

type credentials struct {
	allowedUser     string
	allowedPassword string
	deniedUser      string
	deniedPassword  string
	badPassword     string
}

type outcome struct {
	Operation string `json:"operation"`
	Result    string `json:"result"`
	Error     string `json:"error,omitempty"`
}

type check struct {
	Variant  string    `json:"variant"`
	Endpoint string    `json:"endpoint"`
	Passed   bool      `json:"passed"`
	Oracle   string    `json:"oracle"`
	Outcomes []outcome `json:"outcomes"`
}

type report struct {
	SchemaVersion int     `json:"schema_version"`
	BehaviorID    string  `json:"behavior_id"`
	Scenario      string  `json:"scenario"`
	CreatedAt     string  `json:"created_at"`
	Runtime       string  `json:"runtime"`
	Checks        []check `json:"checks"`
	Passed        bool    `json:"passed"`
}

func loadCredentials() (credentials, error) {
	value := credentials{
		allowedUser: os.Getenv(allowedUserEnv), allowedPassword: os.Getenv(allowedPasswordEnv),
		deniedUser: os.Getenv(deniedUserEnv), deniedPassword: os.Getenv(deniedPasswordEnv),
		badPassword: os.Getenv(badPasswordEnv),
	}
	if value.allowedUser == "" || value.allowedPassword == "" || value.deniedUser == "" || value.deniedPassword == "" || value.badPassword == "" {
		return credentials{}, errors.New("LDAP security credentials must be supplied through the dedicated environment variables")
	}
	return value, nil
}

func dial(endpoint, username, password string) (*amqp.Connection, error) {
	config := amqp.Config{
		SASL:  []amqp.Authentication{&amqp.PlainAuth{Username: username, Password: password}},
		Vhost: "/",
	}
	return amqp.DialConfig("amqp://"+endpoint, config)
}

func redact(message string, secrets credentials) string {
	redacted := message
	for _, secret := range []string{secrets.allowedPassword, secrets.deniedPassword, secrets.badPassword} {
		if secret != "" {
			redacted = strings.ReplaceAll(redacted, secret, "[redacted]")
		}
	}
	return redacted
}

func connectOutcome(endpoint, username, password, operation string, secrets credentials) (*amqp.Connection, outcome) {
	connection, err := dial(endpoint, username, password)
	item := outcome{Operation: operation}
	if err != nil {
		item.Result = "rejected"
		item.Error = redact(err.Error(), secrets)
		return nil, item
	}
	item.Result = "accepted"
	return connection, item
}

func authenticationCheck(variant, endpoint string, secrets credentials) check {
	allowed, allowedOutcome := connectOutcome(endpoint, secrets.allowedUser, secrets.allowedPassword, "allowed-credential-connect", secrets)
	if allowed != nil {
		_ = allowed.Close()
	}
	wrong, wrongOutcome := connectOutcome(endpoint, secrets.allowedUser, secrets.badPassword, "wrong-credential-connect", secrets)
	if wrong != nil {
		_ = wrong.Close()
	}
	passed := allowedOutcome.Result == "accepted" && wrongOutcome.Result == "rejected"
	return check{
		Variant: variant, Endpoint: endpoint, Passed: passed,
		Oracle:   "同一LDAP userの正しいCredentialだけが接続を確立し、誤Credentialは拒否される",
		Outcomes: []outcome{allowedOutcome, wrongOutcome},
	}
}

func declareOutcome(connection *amqp.Connection, operation string, secrets credentials) outcome {
	item := outcome{Operation: operation}
	channel, err := connection.Channel()
	if err != nil {
		item.Result = "rejected"
		item.Error = redact(err.Error(), secrets)
		return item
	}
	defer channel.Close()
	_, err = channel.QueueDeclare("", false, true, true, false, nil)
	if err != nil {
		item.Result = "rejected"
		item.Error = redact(err.Error(), secrets)
		return item
	}
	item.Result = "accepted"
	return item
}

func authorizationCheck(variant, endpoint string, secrets credentials) check {
	allowed, allowedConnect := connectOutcome(endpoint, secrets.allowedUser, secrets.allowedPassword, "resource-group-connect", secrets)
	allowedDeclare := outcome{Operation: "resource-group-queue-declare", Result: "not-run"}
	if allowed != nil {
		allowedDeclare = declareOutcome(allowed, allowedDeclare.Operation, secrets)
		_ = allowed.Close()
	}

	denied, deniedConnect := connectOutcome(endpoint, secrets.deniedUser, secrets.deniedPassword, "vhost-only-group-connect", secrets)
	deniedDeclare := outcome{Operation: "vhost-only-group-queue-declare", Result: "not-run"}
	if denied != nil {
		deniedDeclare = declareOutcome(denied, deniedDeclare.Operation, secrets)
		_ = denied.Close()
	}
	passed := allowedConnect.Result == "accepted" && allowedDeclare.Result == "accepted" &&
		deniedConnect.Result == "accepted" && deniedDeclare.Result == "rejected"
	return check{
		Variant: variant, Endpoint: endpoint, Passed: passed,
		Oracle:   "両userのLDAP認証とvhost接続は成功し、resource group所属userだけがqueueを宣言できる",
		Outcomes: []outcome{allowedConnect, allowedDeclare, deniedConnect, deniedDeclare},
	}
}

func run(mode string, endpoints []string, secrets credentials) (report, error) {
	if len(endpoints) != 3 {
		return report{}, fmt.Errorf("three endpoints are required, got %d", len(endpoints))
	}
	if mode != "authentication" && mode != "authorization" {
		return report{}, fmt.Errorf("unsupported mode %q", mode)
	}
	checks := make([]check, 0, len(endpoints))
	passed := true
	for index, endpoint := range endpoints {
		variant := fmt.Sprintf("node-%d", index+1)
		var item check
		if mode == "authentication" {
			item = authenticationCheck(variant, endpoint, secrets)
		} else {
			item = authorizationCheck(variant, endpoint, secrets)
		}
		checks = append(checks, item)
		passed = passed && item.Passed
	}
	result := report{
		SchemaVersion: 1, BehaviorID: "ldap." + mode, Scenario: "security",
		CreatedAt: time.Now().UTC().Format(time.RFC3339Nano),
		Runtime:   "RabbitMQ 4.3.5 LDAP backend with OpenLDAP 2.6.10; three broker nodes",
		Checks:    checks, Passed: passed,
	}
	if !passed {
		return result, fmt.Errorf("ldap.%s security oracle failed", mode)
	}
	return result, nil
}

func main() {
	mode := flag.String("mode", "", "authentication or authorization")
	endpointFlag := flag.String("endpoints", "127.0.0.1:26672,127.0.0.1:26673,127.0.0.1:26674", "comma-separated RabbitMQ endpoints without credentials")
	flag.Parse()
	secrets, err := loadCredentials()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	result, err := run(*mode, strings.Split(*endpointFlag, ","), secrets)
	if err != nil {
		if data, marshalErr := json.MarshalIndent(result, "", "  "); marshalErr == nil {
			fmt.Fprintln(os.Stderr, string(data))
		}
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println(string(data))
}
