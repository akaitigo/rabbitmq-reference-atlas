package main

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type check struct {
	Endpoint string         `json:"endpoint"`
	Passed   bool           `json:"passed"`
	Oracle   string         `json:"oracle"`
	Observed map[string]any `json:"observed"`
}

type artifact struct {
	SchemaVersion int     `json:"schema_version"`
	BehaviorID    string  `json:"behavior_id"`
	Scenario      string  `json:"scenario"`
	CreatedAt     string  `json:"created_at"`
	Runtime       string  `json:"runtime"`
	Checks        []check `json:"checks"`
	Passed        bool    `json:"passed"`
}

func putString(buffer *bytes.Buffer, value string) {
	_ = binary.Write(buffer, binary.BigEndian, uint16(len(value)))
	buffer.WriteString(value)
}

func remainingLength(value int) []byte {
	var encoded []byte
	for {
		part := byte(value % 128)
		value /= 128
		if value > 0 {
			part |= 0x80
		}
		encoded = append(encoded, part)
		if value == 0 {
			return encoded
		}
	}
}

func mqttPacket(packetType byte, body []byte) []byte {
	return append(append([]byte{packetType}, remainingLength(len(body))...), body...)
}

func readMQTT(connection net.Conn) (byte, []byte, error) {
	var first [1]byte
	if _, err := io.ReadFull(connection, first[:]); err != nil {
		return 0, nil, err
	}
	length, multiplier := 0, 1
	for i := 0; i < 4; i++ {
		var part [1]byte
		if _, err := io.ReadFull(connection, part[:]); err != nil {
			return 0, nil, err
		}
		length += int(part[0]&127) * multiplier
		if part[0]&128 == 0 {
			body := make([]byte, length)
			_, err := io.ReadFull(connection, body)
			return first[0], body, err
		}
		multiplier *= 128
	}
	return 0, nil, errors.New("invalid MQTT remaining length")
}

func connectMQTT(endpoint string, level byte, clientID, password string) (net.Conn, byte, error) {
	connection, err := net.DialTimeout("tcp", endpoint, 4*time.Second)
	if err != nil {
		return nil, 0, err
	}
	_ = connection.SetDeadline(time.Now().Add(8 * time.Second))
	body := new(bytes.Buffer)
	protocol := "MQTT"
	if level == 3 {
		protocol = "MQIsdp"
	}
	putString(body, protocol)
	body.WriteByte(level)
	body.WriteByte(0xC2) // username, password, clean session
	body.Write([]byte{0, 30})
	if level == 5 {
		body.WriteByte(0)
	} // CONNECT properties
	putString(body, clientID)
	putString(body, "atlas")
	putString(body, password)
	if _, err := connection.Write(mqttPacket(0x10, body.Bytes())); err != nil {
		connection.Close()
		return nil, 0, err
	}
	packetType, response, err := readMQTT(connection)
	if err != nil {
		connection.Close()
		return nil, 0, err
	}
	if packetType>>4 != 2 || len(response) < 2 {
		connection.Close()
		return nil, 0, fmt.Errorf("unexpected CONNACK: type=%x body=%x", packetType, response)
	}
	reasonIndex := 1
	if level == 5 {
		reasonIndex = 1
	}
	reason := response[reasonIndex]
	return connection, reason, nil
}

func mqttRoundTrip(endpoint string, level, qos byte, suffix string) error {
	topic := "atlas/definitive/" + suffix
	subscriber, reason, err := connectMQTT(endpoint, level, "sub-"+suffix, "atlas-local-only")
	if err != nil {
		return err
	}
	defer subscriber.Close()
	if reason != 0 {
		return fmt.Errorf("subscriber CONNACK reason=%d", reason)
	}
	sub := new(bytes.Buffer)
	sub.Write([]byte{0, 1})
	if level == 5 {
		sub.WriteByte(0)
	}
	putString(sub, topic)
	sub.WriteByte(qos)
	if _, err := subscriber.Write(mqttPacket(0x82, sub.Bytes())); err != nil {
		return err
	}
	typeByte, ack, err := readMQTT(subscriber)
	if err != nil || typeByte>>4 != 9 || len(ack) < 3 {
		return fmt.Errorf("SUBACK failed: type=%x body=%x err=%v", typeByte, ack, err)
	}

	publisher, reason, err := connectMQTT(endpoint, level, "pub-"+suffix, "atlas-local-only")
	if err != nil {
		return err
	}
	defer publisher.Close()
	if reason != 0 {
		return fmt.Errorf("publisher CONNACK reason=%d", reason)
	}
	payload := "payload-" + suffix
	pub := new(bytes.Buffer)
	putString(pub, topic)
	if qos == 1 {
		pub.Write([]byte{0, 7})
	}
	if level == 5 {
		pub.WriteByte(0)
	}
	pub.WriteString(payload)
	packetType := byte(0x30)
	if qos == 1 {
		packetType = 0x32
	}
	if _, err := publisher.Write(mqttPacket(packetType, pub.Bytes())); err != nil {
		return err
	}
	if qos == 1 {
		t, _, err := readMQTT(publisher)
		if err != nil || t>>4 != 4 {
			return fmt.Errorf("PUBACK failed: type=%x err=%v", t, err)
		}
	}
	t, delivered, err := readMQTT(subscriber)
	if err != nil || t>>4 != 3 {
		return fmt.Errorf("PUBLISH delivery failed: type=%x err=%v", t, err)
	}
	if !bytes.Contains(delivered, []byte(payload)) {
		return fmt.Errorf("payload mismatch: %x", delivered)
	}
	return nil
}

func mqttCheck(endpoint, scenario string) check {
	item := check{Endpoint: endpoint, Oracle: scenario, Observed: map[string]any{}}
	var err error
	switch scenario {
	case "normal":
		err = mqttRoundTrip(endpoint, 4, 1, "qos1-"+strings.ReplaceAll(endpoint, ":", "-"))
		item.Observed = map[string]any{"mqtt_version": "3.1.1", "qos": 1, "publish_subscribe": err == nil}
	case "boundary":
		err = mqttRoundTrip(endpoint, 5, 0, "qos0-"+strings.ReplaceAll(endpoint, ":", "-"))
		item.Observed = map[string]any{"mqtt_version": "5.0", "qos": 0, "publish_subscribe": err == nil}
	case "rejection":
		connection, reason, connectErr := connectMQTT(endpoint, 4, "bad-password", "wrong")
		if connection != nil {
			connection.Close()
		}
		if connectErr == nil && reason != 0 {
			item.Passed = true
			item.Observed = map[string]any{"connack_reason": reason}
			return item
		}
		err = fmt.Errorf("bad credential not rejected: reason=%d err=%v", reason, connectErr)
	case "compatibility":
		versions := map[string]byte{"3.1": 3, "3.1.1": 4, "5.0": 5}
		observed := map[string]any{}
		for name, level := range versions {
			connection, reason, connectErr := connectMQTT(endpoint, level, "compat-"+strings.ReplaceAll(name, ".", ""), "atlas-local-only")
			if connection != nil {
				connection.Close()
			}
			observed[name] = reason == 0 && connectErr == nil
			if connectErr != nil || reason != 0 {
				err = fmt.Errorf("%s rejected: reason=%d err=%v", name, reason, connectErr)
				break
			}
		}
		item.Observed = observed
	}
	item.Passed = err == nil
	if err != nil {
		item.Observed["error"] = err.Error()
	}
	return item
}

func stompFrame(endpoint, acceptVersion, password string) (map[string]string, string, error) {
	connection, err := net.DialTimeout("tcp", endpoint, 4*time.Second)
	if err != nil {
		return nil, "", err
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(8 * time.Second))
	frame := fmt.Sprintf("CONNECT\naccept-version:%s\nhost:/\nlogin:atlas\npasscode:%s\nheart-beat:0,0\n\n\x00", acceptVersion, password)
	if _, err := connection.Write([]byte(frame)); err != nil {
		return nil, "", err
	}
	data, err := bufio.NewReader(connection).ReadBytes(0)
	if err != nil {
		return nil, "", err
	}
	lines := strings.Split(strings.TrimSuffix(string(data), "\x00"), "\n")
	headers := map[string]string{}
	for _, line := range lines[1:] {
		if pair := strings.SplitN(line, ":", 2); len(pair) == 2 {
			headers[pair[0]] = pair[1]
		}
	}
	return headers, lines[0], nil
}

func stompCheck(endpoint, scenario string) check {
	item := check{Endpoint: endpoint, Oracle: scenario, Observed: map[string]any{}}
	var versions []string
	password := "atlas-local-only"
	switch scenario {
	case "normal":
		versions = []string{"1.2"}
	case "boundary":
		versions = []string{"1.0"}
	case "rejection":
		versions = []string{"1.2"}
		password = "wrong"
	case "compatibility":
		versions = []string{"1.0", "1.1", "1.2"}
	}
	observed := map[string]any{}
	for _, version := range versions {
		headers, command, err := stompFrame(endpoint, version, password)
		if err != nil {
			item.Observed = map[string]any{"error": err.Error()}
			return item
		}
		observed[version] = map[string]any{"command": command, "negotiated_version": headers["version"]}
		if scenario == "rejection" {
			item.Passed = command == "ERROR"
			item.Observed = observed
			return item
		}
		if command != "CONNECTED" || (version != "1.0" && headers["version"] != version) {
			item.Observed = observed
			return item
		}
	}
	item.Passed = true
	item.Observed = observed
	return item
}

func writeArtifact(outputDir, behaviorID, scenario string, checks []check) error {
	passed := len(checks) == 3
	for _, item := range checks {
		passed = passed && item.Passed
	}
	document := artifact{1, behaviorID, scenario, time.Now().UTC().Format(time.RFC3339Nano), "RabbitMQ 4.3.5 official plugins; three nodes", checks, passed}
	if !passed {
		return fmt.Errorf("%s:%s oracle failed", behaviorID, scenario)
	}
	data, err := json.MarshalIndent(document, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(outputDir, "definitive."+behaviorID+"."+scenario+".json"), append(data, '\n'), 0o644)
}

func main() {
	mqttEndpoints := flag.String("mqtt-endpoints", "rabbitmq-1:1883,rabbitmq-2:1883,rabbitmq-3:1883", "MQTT endpoints")
	stompEndpoints := flag.String("stomp-endpoints", "rabbitmq-1:61613,rabbitmq-2:61613,rabbitmq-3:61613", "STOMP endpoints")
	outputDir := flag.String("output-dir", "/tmp/rabbitmq-plugin-protocols", "artifact directory")
	flag.Parse()
	if err := os.MkdirAll(*outputDir, 0o755); err != nil {
		panic(err)
	}
	for _, scenario := range []string{"normal", "boundary", "rejection", "compatibility"} {
		var mqttChecks, stompChecks []check
		for _, endpoint := range strings.Split(*mqttEndpoints, ",") {
			mqttChecks = append(mqttChecks, mqttCheck(endpoint, scenario))
		}
		for _, endpoint := range strings.Split(*stompEndpoints, ",") {
			stompChecks = append(stompChecks, stompCheck(endpoint, scenario))
		}
		if err := writeArtifact(*outputDir, "mqtt.protocol-versions-qos", scenario, mqttChecks); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if err := writeArtifact(*outputDir, "stomp.protocol-plugin", scenario, stompChecks); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
}
