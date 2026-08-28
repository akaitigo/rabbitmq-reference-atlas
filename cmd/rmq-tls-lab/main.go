package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/url"
	"os"
	"strings"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const targetVersion = "4.3.5"

type check struct {
	Name     string         `json:"name"`
	Passed   bool           `json:"passed"`
	Observed map[string]any `json:"observed"`
}

type report struct {
	SchemaVersion     int     `json:"schema_version"`
	Mode              string  `json:"mode"`
	CreatedAt         string  `json:"created_at"`
	RabbitMQVersion   string  `json:"rabbitmq_version"`
	CAFingerprint     string  `json:"ca_fingerprint_sha256"`
	ClientFingerprint string  `json:"client_fingerprint_sha256"`
	ServerFingerprint string  `json:"server_fingerprint_sha256"`
	RemoteFingerprint string  `json:"remote_server_fingerprint_sha256"`
	Checks            []check `json:"checks"`
	Passed            bool    `json:"passed"`
}

func main() {
	endpoint := flag.String("endpoint", "127.0.0.1:45671", "TLS AMQP endpoint")
	plaintextEndpoint := flag.String("plaintext-endpoint", "127.0.0.1:45672", "disabled plaintext AMQP endpoint probe")
	serverName := flag.String("server-name", "rabbitmq-tls", "expected server certificate DNS name")
	caPath := flag.String("ca", "", "trusted CA certificate")
	badCAPath := flag.String("bad-ca", "", "untrusted CA certificate")
	clientCertPath := flag.String("client-cert", "", "mTLS client certificate")
	clientKeyPath := flag.String("client-key", "", "mTLS client private key")
	serverCertPath := flag.String("server-cert", "", "expected server certificate for fingerprint binding")
	output := flag.String("output", "", "JSON output path")
	flag.Parse()

	r, err := run(*endpoint, *plaintextEndpoint, *serverName, *caPath, *badCAPath, *clientCertPath, *clientKeyPath, *serverCertPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "TLS Labを実行できません: %v\n", err)
		os.Exit(2)
	}
	if err := writeReport(*output, r); err != nil {
		fmt.Fprintf(os.Stderr, "TLS Evidenceを書き込めません: %v\n", err)
		os.Exit(2)
	}
	if !r.Passed {
		os.Exit(1)
	}
}

func run(endpoint, plaintextEndpoint, serverName, caPath, badCAPath, clientCertPath, clientKeyPath, serverCertPath string) (report, error) {
	for label, value := range map[string]string{
		"ca": caPath, "bad-ca": badCAPath, "client-cert": clientCertPath,
		"client-key": clientKeyPath, "server-cert": serverCertPath,
	} {
		if value == "" {
			return report{}, fmt.Errorf("%s path is required", label)
		}
	}

	caFingerprint, err := certificateFingerprint(caPath)
	if err != nil {
		return report{}, fmt.Errorf("CA fingerprint: %w", err)
	}
	clientFingerprint, err := certificateFingerprint(clientCertPath)
	if err != nil {
		return report{}, fmt.Errorf("client fingerprint: %w", err)
	}
	serverFingerprint, err := certificateFingerprint(serverCertPath)
	if err != nil {
		return report{}, fmt.Errorf("server fingerprint: %w", err)
	}

	trustedConfig, err := clientTLSConfig(caPath, clientCertPath, clientKeyPath, serverName)
	if err != nil {
		return report{}, err
	}
	remoteFingerprint, err := remoteCertificateFingerprint(endpoint, trustedConfig)
	if err != nil {
		return report{}, fmt.Errorf("trusted TLS handshake: %w", err)
	}

	r := report{
		SchemaVersion:     1,
		Mode:              "security-tls-mtls",
		CreatedAt:         time.Now().UTC().Format(time.RFC3339),
		RabbitMQVersion:   targetVersion,
		CAFingerprint:     caFingerprint,
		ClientFingerprint: clientFingerprint,
		ServerFingerprint: serverFingerprint,
		RemoteFingerprint: remoteFingerprint,
	}
	r.Checks = append(r.Checks, check{
		Name:   "tls.server-certificate-pinned",
		Passed: remoteFingerprint == serverFingerprint,
		Observed: map[string]any{
			"fingerprints_match": remoteFingerprint == serverFingerprint,
		},
	})

	connection, version, messagingOK := trustedAMQP(endpoint, trustedConfig)
	r.Checks = append(r.Checks, check{
		Name:   "tls.mtls-trusted-ca-and-hostname",
		Passed: connection != nil && version == targetVersion && messagingOK,
		Observed: map[string]any{
			"connected": connection != nil, "expected_version": targetVersion,
			"observed_version": version, "publish_confirm_consume_ack": messagingOK,
		},
	})
	if connection != nil {
		_ = connection.Close()
	}

	badCAConfig, err := clientTLSConfig(badCAPath, clientCertPath, clientKeyPath, serverName)
	if err != nil {
		return report{}, err
	}
	wrongCAConnection, wrongCAErr := tlsHandshake(endpoint, badCAConfig)
	if wrongCAConnection != nil {
		_ = wrongCAConnection.Close()
	}
	r.Checks = append(r.Checks, rejectedCheck("tls.untrusted-ca-rejected", wrongCAErr))

	wrongNameConfig, err := clientTLSConfig(caPath, clientCertPath, clientKeyPath, "wrong.rabbitmq.invalid")
	if err != nil {
		return report{}, err
	}
	wrongNameConnection, wrongNameErr := tlsHandshake(endpoint, wrongNameConfig)
	if wrongNameConnection != nil {
		_ = wrongNameConnection.Close()
	}
	r.Checks = append(r.Checks, rejectedCheck("tls.hostname-mismatch-rejected", wrongNameErr))

	noClientConfig, err := clientTLSConfig(caPath, "", "", serverName)
	if err != nil {
		return report{}, err
	}
	noClientConnection, _, noClientOK := trustedAMQP(endpoint, noClientConfig)
	if noClientConnection != nil {
		_ = noClientConnection.Close()
	}
	r.Checks = append(r.Checks, check{
		Name:     "tls.client-certificate-required",
		Passed:   !noClientOK,
		Observed: map[string]any{"connection_rejected": !noClientOK},
	})

	plainConnection, plainErr := amqp.DialConfig(plainURI(plaintextEndpoint), amqp.Config{
		Dial: amqp.DefaultDial(2 * time.Second),
	})
	if plainConnection != nil {
		_ = plainConnection.Close()
	}
	r.Checks = append(r.Checks, check{
		Name:   "tls.plaintext-listener-disabled",
		Passed: plainErr != nil,
		Observed: map[string]any{
			"plaintext_amqp_rejected": plainErr != nil,
		},
	})

	r.Passed = true
	for _, item := range r.Checks {
		r.Passed = r.Passed && item.Passed
	}
	return r, nil
}

func clientTLSConfig(caPath, certPath, keyPath, serverName string) (*tls.Config, error) {
	caPEM, err := os.ReadFile(caPath)
	if err != nil {
		return nil, fmt.Errorf("read CA: %w", err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("CA PEM does not contain a certificate")
	}
	config := &tls.Config{
		RootCAs:    roots,
		ServerName: serverName,
		MinVersion: tls.VersionTLS12,
	}
	if certPath != "" || keyPath != "" {
		if certPath == "" || keyPath == "" {
			return nil, errors.New("both client certificate and key are required")
		}
		certificate, err := tls.LoadX509KeyPair(certPath, keyPath)
		if err != nil {
			return nil, fmt.Errorf("load client key pair: %w", err)
		}
		config.Certificates = []tls.Certificate{certificate}
	}
	return config, nil
}

func tlsHandshake(endpoint string, config *tls.Config) (*tls.Conn, error) {
	dialer := &net.Dialer{Timeout: 5 * time.Second}
	connection, err := tls.DialWithDialer(dialer, "tcp", endpoint, config.Clone())
	if err != nil {
		return nil, err
	}
	return connection, nil
}

func remoteCertificateFingerprint(endpoint string, config *tls.Config) (string, error) {
	connection, err := tlsHandshake(endpoint, config)
	if err != nil {
		return "", err
	}
	defer connection.Close()
	certificates := connection.ConnectionState().PeerCertificates
	if len(certificates) == 0 {
		return "", errors.New("server did not provide a certificate")
	}
	return fingerprint(certificates[0].Raw), nil
}

func trustedAMQP(endpoint string, config *tls.Config) (*amqp.Connection, string, bool) {
	connection, err := amqp.DialConfig(secureURI(endpoint), amqp.Config{
		TLSClientConfig: config.Clone(),
		Heartbeat:       5 * time.Second,
		Dial:            amqp.DefaultDial(5 * time.Second),
	})
	if err != nil {
		return nil, "", false
	}
	version, _ := connection.Properties["version"].(string)
	if err := messagingProbe(connection); err != nil {
		_ = connection.Close()
		return nil, version, false
	}
	return connection, version, true
}

func messagingProbe(connection *amqp.Connection) error {
	channel, err := connection.Channel()
	if err != nil {
		return err
	}
	defer channel.Close()
	queue, err := channel.QueueDeclare("", false, true, true, false, nil)
	if err != nil {
		return err
	}
	if err := channel.Confirm(false); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	confirmation, err := channel.PublishWithDeferredConfirmWithContext(ctx, "", queue.Name, false, false, amqp.Publishing{
		MessageId: "rabbitmq-reference-atlas-tls-probe",
		Body:      []byte("tls-probe"),
	})
	if err != nil {
		return err
	}
	if confirmation == nil {
		return errors.New("publisher confirmation was not registered")
	}
	ack, err := confirmation.WaitContext(ctx)
	if err != nil || !ack {
		return fmt.Errorf("publisher confirmation failed: ack=%v error=%w", ack, err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		delivery, ok, err := channel.Get(queue.Name, false)
		if err != nil {
			return err
		}
		if ok {
			if delivery.MessageId != "rabbitmq-reference-atlas-tls-probe" {
				return errors.New("unexpected message identifier")
			}
			return delivery.Ack(false)
		}
		time.Sleep(100 * time.Millisecond)
	}
	return errors.New("TLS messaging probe timed out")
}

func certificateFingerprint(path string) (string, error) {
	contents, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	block, _ := pem.Decode(contents)
	if block == nil || block.Type != "CERTIFICATE" {
		return "", errors.New("PEM certificate not found")
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return "", err
	}
	return fingerprint(certificate.Raw), nil
}

func fingerprint(raw []byte) string {
	digest := sha256.Sum256(raw)
	return "sha256:" + strings.ToLower(hex.EncodeToString(digest[:]))
}

func rejectedCheck(name string, err error) check {
	return check{Name: name, Passed: err != nil, Observed: map[string]any{"tls_handshake_rejected": err != nil}}
}

func secureURI(endpoint string) string {
	u := &url.URL{Scheme: "amqps", Host: endpoint, Path: "/", User: url.UserPassword("atlas", "atlas-local-only")}
	return u.String()
}

func plainURI(endpoint string) string {
	u := &url.URL{Scheme: "amqp", Host: endpoint, Path: "/", User: url.UserPassword("atlas", "atlas-local-only")}
	return u.String()
}

func writeReport(path string, value report) error {
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	encoded = append(encoded, '\n')
	if path == "" {
		_, err = os.Stdout.Write(encoded)
		return err
	}
	return os.WriteFile(path, encoded, 0o644)
}
