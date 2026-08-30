package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	allowedUserEnv     = "RABBITMQ_LDAP_ALLOWED_USER"
	allowedPasswordEnv = "RABBITMQ_LDAP_ALLOWED_PASSWORD"
	badPasswordEnv     = "RABBITMQ_LDAP_BAD_PASSWORD"
)

type credentials struct {
	user, password, badPassword string
}

type observation struct {
	Operation string `json:"operation"`
	Result    string `json:"result"`
	Status    int    `json:"status,omitempty"`
	Error     string `json:"error,omitempty"`
}

type check struct {
	Variant  string        `json:"variant"`
	Endpoint string        `json:"endpoint"`
	Vhost    string        `json:"vhost"`
	Passed   bool          `json:"passed"`
	Oracle   string        `json:"oracle"`
	Outcomes []observation `json:"outcomes"`
}

type report struct {
	SchemaVersion int     `json:"schema_version"`
	CreatedAt     string  `json:"created_at"`
	Runtime       string  `json:"runtime"`
	Checks        []check `json:"checks"`
	Passed        bool    `json:"passed"`
}

func loadCredentials() (credentials, error) {
	value := credentials{os.Getenv(allowedUserEnv), os.Getenv(allowedPasswordEnv), os.Getenv(badPasswordEnv)}
	if value.user == "" || value.password == "" || value.badPassword == "" {
		return credentials{}, errors.New("security-002 LDAP credentials must be supplied through dedicated environment variables")
	}
	return value, nil
}

func redact(message string, secrets credentials) string {
	for _, secret := range []string{secrets.password, secrets.badPassword} {
		message = strings.ReplaceAll(message, secret, "[redacted]")
	}
	return message
}

func dial(endpoint, vhost, username, password string) (*amqp.Connection, error) {
	config := amqp.Config{
		SASL:  []amqp.Authentication{&amqp.PlainAuth{Username: username, Password: password}},
		Vhost: vhost,
	}
	return amqp.DialConfig("amqp://"+endpoint, config)
}

func connect(endpoint, vhost, username, password, name string, secrets credentials) (*amqp.Connection, observation) {
	connection, err := dial(endpoint, vhost, username, password)
	item := observation{Operation: name}
	if err != nil {
		item.Result = "rejected"
		item.Error = redact(err.Error(), secrets)
		return nil, item
	}
	item.Result = "accepted"
	return connection, item
}

func declare(channel *amqp.Channel, queue, operation string, secrets credentials) observation {
	item := observation{Operation: operation}
	_, err := channel.QueueDeclare(queue, false, false, false, false, nil)
	if err != nil {
		item.Result = "rejected"
		item.Error = redact(err.Error(), secrets)
		return item
	}
	item.Result = "accepted"
	return item
}

func limitMutation(base, vhost string, secrets credentials) observation {
	path := "/api/vhost-limits/" + url.PathEscape(vhost) + "/max-queues"
	request, err := http.NewRequest(http.MethodPut, base+path, bytes.NewBufferString(`{"value":2}`))
	if err != nil {
		return observation{Operation: "unprivileged-limit-mutation", Result: "error", Error: err.Error()}
	}
	request.SetBasicAuth(secrets.user, secrets.password)
	request.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 8 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		return observation{Operation: "unprivileged-limit-mutation", Result: "error", Error: redact(err.Error(), secrets)}
	}
	defer response.Body.Close()
	result := "accepted"
	if response.StatusCode == http.StatusUnauthorized || response.StatusCode == http.StatusForbidden {
		result = "rejected"
	}
	return observation{Operation: "unprivileged-limit-mutation", Result: result, Status: response.StatusCode}
}

func run(endpoints, managementURLs, vhosts []string, secrets credentials) (report, error) {
	if len(endpoints) != 3 || len(managementURLs) != 3 || len(vhosts) != 3 {
		return report{}, errors.New("three endpoints, management URLs and vhosts are required")
	}
	result := report{SchemaVersion: 1, CreatedAt: time.Now().UTC().Format(time.RFC3339Nano), Runtime: "RabbitMQ 4.3.5 LDAP-over-TLS three-node cluster"}
	result.Passed = true
	for index := range endpoints {
		variant := fmt.Sprintf("node-%d", index+1)
		allowed, allowedConnect := connect(endpoints[index], vhosts[index], secrets.user, secrets.password, "ldap-tls-correct-credential", secrets)
		wrong, wrongConnect := connect(endpoints[index], vhosts[index], secrets.user, secrets.badPassword, "ldap-tls-wrong-credential", secrets)
		if wrong != nil {
			_ = wrong.Close()
		}
		first := observation{Operation: "queue-below-limit", Result: "not-run"}
		second := observation{Operation: "queue-over-limit", Result: "not-run"}
		if allowed != nil {
			channel, err := allowed.Channel()
			if err != nil {
				first = observation{Operation: first.Operation, Result: "error", Error: redact(err.Error(), secrets)}
			} else {
				first = declare(channel, "security-002-first", first.Operation, secrets)
				second = declare(channel, "security-002-second", second.Operation, secrets)
				_ = channel.Close()
			}
			_ = allowed.Close()
		}
		mutation := limitMutation(managementURLs[index], vhosts[index], secrets)
		passed := allowedConnect.Result == "accepted" && wrongConnect.Result == "rejected" &&
			first.Result == "accepted" && second.Result == "rejected" && mutation.Result == "rejected"
		result.Checks = append(result.Checks, check{
			Variant: variant, Endpoint: endpoints[index], Vhost: vhosts[index], Passed: passed,
			Oracle:   "LDAP-over-TLS認証を通過した最小権限userでもvhost queue上限と管理API認可を回避できない",
			Outcomes: []observation{allowedConnect, wrongConnect, first, second, mutation},
		})
		result.Passed = result.Passed && passed
	}
	if !result.Passed {
		return result, errors.New("security-002 LDAP TLS or queue-limit oracle failed")
	}
	return result, nil
}

func split(value string) []string { return strings.Split(value, ",") }

func main() {
	endpoints := flag.String("endpoints", "127.0.0.1:27672,127.0.0.1:27673,127.0.0.1:27674", "comma-separated AMQP endpoints")
	management := flag.String("management-urls", "http://127.0.0.1:36672,http://127.0.0.1:36673,http://127.0.0.1:36674", "comma-separated Management URLs")
	vhosts := flag.String("vhosts", "", "comma-separated dedicated vhosts")
	flag.Parse()
	secrets, err := loadCredentials()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	result, runErr := run(split(*endpoints), split(*management), split(*vhosts), secrets)
	data, marshalErr := json.MarshalIndent(result, "", "  ")
	if marshalErr != nil {
		fmt.Fprintln(os.Stderr, marshalErr)
		os.Exit(1)
	}
	if runErr != nil {
		fmt.Fprintln(os.Stderr, string(data))
		fmt.Fprintln(os.Stderr, runErr)
		os.Exit(1)
	}
	fmt.Println(string(data))
}
