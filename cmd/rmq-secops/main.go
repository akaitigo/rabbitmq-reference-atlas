package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	accessRefused = 403
	targetVersion = "4.3.5"
)

type check struct {
	Name     string         `json:"name"`
	Category string         `json:"category"`
	Passed   bool           `json:"passed"`
	Observed map[string]any `json:"observed,omitempty"`
	Error    string         `json:"error,omitempty"`
}

type report struct {
	SchemaVersion int     `json:"schema_version"`
	Mode          string  `json:"mode"`
	RunID         string  `json:"run_id"`
	CreatedAt     string  `json:"created_at"`
	RabbitMQ      string  `json:"rabbitmq_version"`
	Namespace     string  `json:"namespace"`
	Checks        []check `json:"checks"`
	Passed        bool    `json:"passed"`
	Summary       summary `json:"summary"`
}

type summary struct {
	Passed int `json:"passed"`
	Failed int `json:"failed"`
}

type apiClient struct {
	base     string
	username string
	password string
	http     *http.Client
}

type nodeInfo struct {
	Name          string   `json:"name"`
	Running       bool     `json:"running"`
	MemAlarm      bool     `json:"mem_alarm"`
	DiskFreeAlarm bool     `json:"disk_free_alarm"`
	Partitions    []string `json:"partitions"`
}

type queueInfo struct {
	Name                   string `json:"name"`
	Vhost                  string `json:"vhost"`
	Node                   string `json:"node"`
	Type                   string `json:"type"`
	State                  string `json:"state"`
	Messages               int    `json:"messages"`
	MessagesReady          int    `json:"messages_ready"`
	MessagesUnacknowledged int    `json:"messages_unacknowledged"`
	Consumers              int    `json:"consumers"`
}

type healthInfo struct {
	Status string `json:"status"`
}

func main() {
	amqpEndpoints := flag.String("amqp-urls", "amqp://127.0.0.1:25672/,amqp://127.0.0.1:25673/,amqp://127.0.0.1:25674/", "comma-separated AMQP endpoints without least-privilege credentials")
	managementEndpoints := flag.String("management-urls", "http://127.0.0.1:35672,http://127.0.0.1:35673,http://127.0.0.1:35674", "comma-separated Management API endpoints")
	adminUser := flag.String("admin-user", "atlas", "Management API administrator user")
	adminPassword := flag.String("admin-password", "atlas-local-only", "Management API administrator password")
	output := flag.String("output", "", "JSON output path (stdout when empty)")
	flag.Parse()

	r := run(split(*amqpEndpoints), split(*managementEndpoints), *adminUser, *adminPassword)
	if err := writeReport(*output, r); err != nil {
		fmt.Fprintf(os.Stderr, "結果JSONを書き込めません: %v\n", err)
		os.Exit(2)
	}
	if !r.Passed {
		os.Exit(1)
	}
}

func run(amqpEndpoints, managementEndpoints []string, adminUser, adminPassword string) report {
	runID := fmt.Sprintf("%d", time.Now().UTC().UnixNano())
	namespace := "atlas-secops-" + runID
	r := report{
		SchemaVersion: 1,
		Mode:          "security-observability-operations",
		RunID:         runID,
		CreatedAt:     time.Now().UTC().Format(time.RFC3339),
		RabbitMQ:      targetVersion,
		Namespace:     namespace,
	}

	if len(amqpEndpoints) == 0 || len(managementEndpoints) == 0 {
		r.Checks = append(r.Checks, failed("input.endpoints", "setup", errors.New("AMQPとManagement APIのendpointが必要です")))
		return finalize(r)
	}

	manager, discovery := discoverManagement(managementEndpoints, adminUser, adminPassword)
	r.Checks = append(r.Checks, discovery)
	if !discovery.Passed {
		return finalize(r)
	}

	vhost := namespace
	username := namespace + "-user"
	password := "local-" + runID
	allowedExchange := "allowed.exchange"
	allowedQueue := "allowed.queue"
	forbiddenQueue := "forbidden.queue"
	createdVhost, createdUser := false, false

	setupCheck, vhostOK, userOK := setupLeastPrivilege(manager, vhost, username, password, adminUser, forbiddenQueue)
	createdVhost, createdUser = vhostOK, userOK
	r.Checks = append(r.Checks, setupCheck)
	if setupCheck.Passed {
		userEndpoints, err := credentialedEndpoints(amqpEndpoints, username, password, vhost)
		if err != nil {
			r.Checks = append(r.Checks, failed("security.credentials", "security", err))
		} else {
			badPasswordEndpoints, _ := credentialedEndpoints(amqpEndpoints, username, password+"-wrong", vhost)
			r.Checks = append(r.Checks, deniedConnection("security.bad-password-denied", badPasswordEndpoints))
			crossVhostEndpoints, _ := credentialedEndpoints(amqpEndpoints, username, password, "/")
			r.Checks = append(r.Checks, deniedConnection("security.cross-vhost-denied", crossVhostEndpoints))
			conn, err := connect(userEndpoints, 30*time.Second)
			if err != nil {
				r.Checks = append(r.Checks, failed("security.authentication", "security", err))
			} else {
				r.Checks = append(r.Checks, passed("security.authentication", "security", map[string]any{
					"user": username, "vhost": vhost, "endpoint_count": len(userEndpoints),
				}))
				r.Checks = append(r.Checks, positiveMessaging(conn, allowedExchange, allowedQueue, runID))
				r.Checks = append(r.Checks, deniedConfigure(conn, forbiddenQueue))
				r.Checks = append(r.Checks, deniedRead(conn, forbiddenQueue))
				_ = conn.Close()
			}
			rotatedPassword := password + "-rotated"
			r.Checks = append(r.Checks, rotateCredential(manager, username, password, rotatedPassword, vhost, amqpEndpoints))
		}

		r.Checks = append(r.Checks, observeNodes(manager))
		r.Checks = append(r.Checks, observeQueue(manager, vhost, allowedQueue))
		for i, endpoint := range managementEndpoints {
			client := newAPIClient(endpoint, adminUser, adminPassword)
			r.Checks = append(r.Checks, observeHealth(client, endpoint, i+1))
			r.Checks = append(r.Checks, observeAlarms(client, endpoint, i+1))
		}
	}

	r.Checks = append(r.Checks, cleanup(manager, vhost, username, createdVhost, createdUser))
	return finalize(r)
}

func deniedConnection(name string, endpoints []string) check {
	observed := map[string]any{"endpoint_count": len(endpoints), "connections_accepted": 0}
	for _, endpoint := range endpoints {
		conn, err := amqp.DialConfig(endpoint, amqp.Config{Heartbeat: 3 * time.Second, Dial: amqp.DefaultDial(3 * time.Second)})
		if err == nil {
			_ = conn.Close()
			observed["connections_accepted"] = observed["connections_accepted"].(int) + 1
		}
	}
	if observed["connections_accepted"].(int) != 0 {
		return failedObserved(name, "security", observed, errors.New("拒否対象Connectionが受理されました"))
	}
	return passed(name, "security", observed)
}

func rotateCredential(client *apiClient, username, oldPassword, newPassword, vhost string, amqpEndpoints []string) check {
	observed := map[string]any{}
	status, err := client.request(http.MethodPut, "/api/users/"+path(username), map[string]any{"password": newPassword, "tags": ""}, nil)
	observed["rotation_status"] = status
	if err != nil || !success(status) {
		return failedObserved("security.credential-rotation", "security", observed, fmt.Errorf("password rotation: status=%d: %w", status, err))
	}
	oldEndpoints, _ := credentialedEndpoints(amqpEndpoints, username, oldPassword, vhost)
	oldDenied := deniedConnection("security.old-password-denied", oldEndpoints)
	observed["old_password_denied"] = oldDenied.Passed
	newEndpoints, _ := credentialedEndpoints(amqpEndpoints, username, newPassword, vhost)
	conn, connectErr := connect(newEndpoints, 10*time.Second)
	observed["new_password_accepted"] = connectErr == nil
	if conn != nil {
		_ = conn.Close()
	}
	if !oldDenied.Passed || connectErr != nil {
		return failedObserved("security.credential-rotation", "security", observed, fmt.Errorf("rotated credential invariant: %v", connectErr))
	}
	return passed("security.credential-rotation", "security", observed)
}

func discoverManagement(endpoints []string, username, password string) (*apiClient, check) {
	var failures []string
	for _, endpoint := range endpoints {
		client := newAPIClient(endpoint, username, password)
		var overview map[string]any
		status, err := client.request(http.MethodGet, "/api/overview", nil, &overview)
		actualVersion, _ := overview["rabbitmq_version"].(string)
		if err == nil && status == http.StatusOK && actualVersion == targetVersion {
			return client, passed("environment.version-lock-management-authentication", "observability", map[string]any{
				"endpoint": safeEndpoint(endpoint), "expected_rabbitmq_version": targetVersion, "observed_rabbitmq_version": actualVersion,
			})
		}
		failures = append(failures, fmt.Sprintf("%s: status=%d version=%q error=%v", safeEndpoint(endpoint), status, actualVersion, err))
	}
	return nil, failed("environment.version-lock-management-authentication", "observability", fmt.Errorf("RabbitMQ %sの利用可能なManagement APIがありません: %s", targetVersion, strings.Join(failures, "; ")))
}

func setupLeastPrivilege(client *apiClient, vhost, username, password, adminUser, forbiddenQueue string) (check, bool, bool) {
	vhostOK, userOK := false, false
	steps := map[string]any{}
	if status, err := client.request(http.MethodPut, "/api/vhosts/"+path(vhost), map[string]any{}, nil); err != nil || !success(status) {
		return failedObserved("security.least-privilege-setup", "security", steps, fmt.Errorf("vhost作成: status=%d: %w", status, err)), vhostOK, userOK
	}
	vhostOK = true
	steps["vhost_created"] = true

	if status, err := client.request(http.MethodPut, "/api/users/"+path(username), map[string]any{"password": password, "tags": ""}, nil); err != nil || !success(status) {
		return failedObserved("security.least-privilege-setup", "security", steps, fmt.Errorf("user作成: status=%d: %w", status, err)), vhostOK, userOK
	}
	userOK = true
	steps["user_created"] = true

	permissions := map[string]any{
		"configure": `^allowed\..*$`,
		"write":     `^allowed\..*$`,
		"read":      `^allowed\..*$`,
	}
	if status, err := client.request(http.MethodPut, "/api/permissions/"+path(vhost)+"/"+path(username), permissions, nil); err != nil || !success(status) {
		return failedObserved("security.least-privilege-setup", "security", steps, fmt.Errorf("least-privilege permission設定: status=%d: %w", status, err)), vhostOK, userOK
	}
	steps["permissions"] = permissions

	adminPermissions := map[string]any{"configure": ".*", "write": ".*", "read": ".*"}
	if status, err := client.request(http.MethodPut, "/api/permissions/"+path(vhost)+"/"+path(adminUser), adminPermissions, nil); err != nil || !success(status) {
		return failedObserved("security.least-privilege-setup", "security", steps, fmt.Errorf("検証用admin permission設定: status=%d: %w", status, err)), vhostOK, userOK
	}

	queueBody := map[string]any{"durable": true, "auto_delete": false, "arguments": map[string]any{}}
	if status, err := client.request(http.MethodPut, "/api/queues/"+path(vhost)+"/"+path(forbiddenQueue), queueBody, nil); err != nil || !success(status) {
		return failedObserved("security.least-privilege-setup", "security", steps, fmt.Errorf("拒否検証Queue作成: status=%d: %w", status, err)), vhostOK, userOK
	}
	steps["forbidden_probe_queue"] = forbiddenQueue
	return passed("security.least-privilege-setup", "security", steps), vhostOK, userOK
}

func positiveMessaging(conn *amqp.Connection, exchange, queue, runID string) check {
	ch, err := conn.Channel()
	if err != nil {
		return failed("security.allowed-operations", "security", err)
	}
	defer ch.Close()
	if err := ch.ExchangeDeclare(exchange, "direct", true, false, false, false, nil); err != nil {
		return failed("security.allowed-operations", "security", err)
	}
	q, err := ch.QueueDeclare(queue, true, false, false, false, nil)
	if err != nil {
		return failed("security.allowed-operations", "security", err)
	}
	if err := ch.QueueBind(q.Name, "allowed", exchange, false, nil); err != nil {
		return failed("security.allowed-operations", "security", err)
	}
	if err := ch.Confirm(false); err != nil {
		return failed("security.allowed-operations", "security", err)
	}
	confirmation, err := ch.PublishWithDeferredConfirmWithContext(context.Background(), exchange, "allowed", false, false, amqp.Publishing{
		DeliveryMode: amqp.Persistent,
		MessageId:    "secops-" + runID,
		Body:         []byte("least-privilege-positive-probe"),
	})
	if err != nil {
		return failed("security.allowed-operations", "security", err)
	}
	if confirmation == nil || !confirmation.Wait() || !confirmation.Acked() {
		return failed("security.allowed-operations", "security", errors.New("Publisher Confirmがackではありません"))
	}
	delivery, ok, err := getEventually(ch, q.Name, 5*time.Second)
	if err != nil || !ok {
		return failed("security.allowed-operations", "security", fmt.Errorf("許可Queueから取得できません: %w", err))
	}
	if err := delivery.Ack(false); err != nil {
		return failed("security.allowed-operations", "security", err)
	}
	return passed("security.allowed-operations", "security", map[string]any{
		"exchange_declare":  exchange,
		"queue_declare":     queue,
		"queue_bind":        true,
		"publisher_confirm": "ack",
		"consume_and_ack":   true,
		"message_id":        delivery.MessageId,
	})
}

func deniedConfigure(conn *amqp.Connection, forbiddenQueue string) check {
	ch, err := conn.Channel()
	if err != nil {
		return failed("security.denied-configure", "security", err)
	}
	defer ch.Close()
	_, operationErr := ch.QueueDeclare(forbiddenQueue+"-new", true, false, false, false, nil)
	code, reason := amqpError(operationErr)
	observed := map[string]any{"operation": "queue.declare", "resource": forbiddenQueue + "-new", "reply_code": code, "reply_text": reason}
	if code != accessRefused {
		return failedObserved("security.denied-configure", "security", observed, fmt.Errorf("ACCESS_REFUSEDを期待しました: %v", operationErr))
	}
	return passed("security.denied-configure", "security", observed)
}

func deniedRead(conn *amqp.Connection, forbiddenQueue string) check {
	ch, err := conn.Channel()
	if err != nil {
		return failed("security.denied-read", "security", err)
	}
	defer ch.Close()
	_, operationErr := ch.Consume(forbiddenQueue, "secops-denied-read", false, false, false, false, nil)
	code, reason := amqpError(operationErr)
	observed := map[string]any{"operation": "basic.consume", "resource": forbiddenQueue, "reply_code": code, "reply_text": reason}
	if code != accessRefused {
		return failedObserved("security.denied-read", "security", observed, fmt.Errorf("ACCESS_REFUSEDを期待しました: %v", operationErr))
	}
	return passed("security.denied-read", "security", observed)
}

func observeNodes(client *apiClient) check {
	var nodes []nodeInfo
	status, err := client.request(http.MethodGet, "/api/nodes", nil, &nodes)
	if err != nil || status != http.StatusOK {
		return failed("observability.management-nodes", "observability", fmt.Errorf("status=%d: %w", status, err))
	}
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].Name < nodes[j].Name })
	allRunning := len(nodes) == 3
	observed := make([]map[string]any, 0, len(nodes))
	for _, node := range nodes {
		allRunning = allRunning && node.Running
		observed = append(observed, map[string]any{
			"name": node.Name, "running": node.Running, "mem_alarm": node.MemAlarm,
			"disk_free_alarm": node.DiskFreeAlarm, "partitions": node.Partitions,
		})
	}
	values := map[string]any{"expected_nodes": 3, "observed_nodes": len(nodes), "nodes": observed}
	if !allRunning {
		return failedObserved("observability.management-nodes", "observability", values, errors.New("3 Nodeすべてがrunningではありません"))
	}
	return passed("observability.management-nodes", "observability", values)
}

func observeQueue(client *apiClient, vhost, queue string) check {
	var info queueInfo
	status, err := client.request(http.MethodGet, "/api/queues/"+path(vhost)+"/"+path(queue), nil, &info)
	if err != nil || status != http.StatusOK {
		return failed("observability.management-queue", "observability", fmt.Errorf("status=%d: %w", status, err))
	}
	observed := map[string]any{
		"name": info.Name, "vhost": info.Vhost, "node": info.Node, "type": info.Type, "state": info.State,
		"messages": info.Messages, "messages_ready": info.MessagesReady,
		"messages_unacknowledged": info.MessagesUnacknowledged, "consumers": info.Consumers,
	}
	valid := info.Name == queue && info.Vhost == vhost && info.Node != "" && info.State == "running" && info.Messages == 0
	if !valid {
		return failedObserved("observability.management-queue", "observability", observed, errors.New("Queue観測値が期待条件を満たしません"))
	}
	return passed("observability.management-queue", "observability", observed)
}

func observeHealth(client *apiClient, endpoint string, ordinal int) check {
	name := fmt.Sprintf("operations.node-health.endpoint-%d", ordinal)
	var info healthInfo
	status, err := client.request(http.MethodGet, "/api/health/checks/ready-to-serve-clients", nil, &info)
	observed := map[string]any{"endpoint": safeEndpoint(endpoint), "http_status": status, "status": info.Status}
	if err != nil || status != http.StatusOK || info.Status != "ok" {
		return failedObserved(name, "operations", observed, healthError("ready-to-serve-clients", status, info.Status, err))
	}
	return passed(name, "operations", observed)
}

func observeAlarms(client *apiClient, endpoint string, ordinal int) check {
	name := fmt.Sprintf("operations.cluster-alarms.endpoint-%d", ordinal)
	var info healthInfo
	status, err := client.request(http.MethodGet, "/api/health/checks/alarms", nil, &info)
	observed := map[string]any{"endpoint": safeEndpoint(endpoint), "http_status": status, "status": info.Status}
	if err != nil || status != http.StatusOK || info.Status != "ok" {
		return failedObserved(name, "operations", observed, healthError("alarms", status, info.Status, err))
	}
	return passed(name, "operations", observed)
}

func cleanup(client *apiClient, vhost, username string, createdVhost, createdUser bool) check {
	observed := map[string]any{"vhost_created": createdVhost, "user_created": createdUser}
	var cleanupErrors []string
	if createdUser {
		status, err := client.request(http.MethodDelete, "/api/users/"+path(username), nil, nil)
		observed["delete_user_status"] = status
		if err != nil || !success(status) {
			cleanupErrors = append(cleanupErrors, fmt.Sprintf("user削除 status=%d error=%v", status, err))
		}
		verifyStatus, verifyErr := client.request(http.MethodGet, "/api/users/"+path(username), nil, nil)
		observed["verify_user_absent_status"] = verifyStatus
		if verifyErr != nil || verifyStatus != http.StatusNotFound {
			cleanupErrors = append(cleanupErrors, fmt.Sprintf("user不存在確認 status=%d error=%v", verifyStatus, verifyErr))
		}
	}
	if createdVhost {
		status, err := client.request(http.MethodDelete, "/api/vhosts/"+path(vhost), nil, nil)
		observed["delete_vhost_status"] = status
		if err != nil || !success(status) {
			cleanupErrors = append(cleanupErrors, fmt.Sprintf("vhost削除 status=%d error=%v", status, err))
		}
		verifyStatus, verifyErr := client.request(http.MethodGet, "/api/vhosts/"+path(vhost), nil, nil)
		observed["verify_vhost_absent_status"] = verifyStatus
		if verifyErr != nil || verifyStatus != http.StatusNotFound {
			cleanupErrors = append(cleanupErrors, fmt.Sprintf("vhost不存在確認 status=%d error=%v", verifyStatus, verifyErr))
		}
	}
	if len(cleanupErrors) > 0 {
		return failedObserved("operations.safe-cleanup", "operations", observed, errors.New(strings.Join(cleanupErrors, "; ")))
	}
	return passed("operations.safe-cleanup", "operations", observed)
}

func newAPIClient(endpoint, username, password string) *apiClient {
	return &apiClient{
		base: strings.TrimRight(endpoint, "/"), username: username, password: password,
		http: &http.Client{Timeout: 10 * time.Second},
	}
}

func (c *apiClient) request(method, requestPath string, body, destination any) (int, error) {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return 0, err
		}
		reader = bytes.NewReader(encoded)
	}
	req, err := http.NewRequest(method, c.base+requestPath, reader)
	if err != nil {
		return 0, err
	}
	req.SetBasicAuth(c.username, c.password)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return resp.StatusCode, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		// 404はcleanupの不存在確認で期待値として扱うためtransport errorにしない。
		if resp.StatusCode == http.StatusNotFound {
			return resp.StatusCode, nil
		}
		return resp.StatusCode, fmt.Errorf("Management API %s %s: %s", method, requestPath, strings.TrimSpace(string(data)))
	}
	if destination != nil && len(data) > 0 {
		if err := json.Unmarshal(data, destination); err != nil {
			return resp.StatusCode, err
		}
	}
	return resp.StatusCode, nil
}

func credentialedEndpoints(endpoints []string, username, password, vhost string) ([]string, error) {
	out := make([]string, 0, len(endpoints))
	for _, endpoint := range endpoints {
		u, err := url.Parse(endpoint)
		if err != nil {
			return nil, err
		}
		if u.Scheme != "amqp" && u.Scheme != "amqps" {
			return nil, fmt.Errorf("未対応AMQP scheme: %q", u.Scheme)
		}
		u.User = url.UserPassword(username, password)
		u.Path = "/" + vhost
		u.RawPath = ""
		out = append(out, u.String())
	}
	return out, nil
}

func connect(endpoints []string, deadline time.Duration) (*amqp.Connection, error) {
	end := time.Now().Add(deadline)
	var last error
	for time.Now().Before(end) {
		for _, endpoint := range endpoints {
			conn, err := amqp.DialConfig(endpoint, amqp.Config{Heartbeat: 5 * time.Second, Dial: amqp.DefaultDial(3 * time.Second)})
			if err == nil {
				return conn, nil
			}
			last = err
		}
		time.Sleep(500 * time.Millisecond)
	}
	return nil, fmt.Errorf("AMQP endpointへ接続できません: %w", last)
}

func getEventually(ch *amqp.Channel, queue string, timeout time.Duration) (amqp.Delivery, bool, error) {
	end := time.Now().Add(timeout)
	for time.Now().Before(end) {
		delivery, ok, err := ch.Get(queue, false)
		if err != nil || ok {
			return delivery, ok, err
		}
		time.Sleep(100 * time.Millisecond)
	}
	return amqp.Delivery{}, false, nil
}

func amqpError(err error) (int, string) {
	var protocolError *amqp.Error
	if errors.As(err, &protocolError) {
		return protocolError.Code, protocolError.Reason
	}
	return 0, fmt.Sprint(err)
}

func path(value string) string { return url.PathEscape(value) }

func safeEndpoint(value string) string {
	u, err := url.Parse(value)
	if err != nil {
		return value
	}
	u.User = nil
	return u.String()
}

func healthError(checkName string, status int, healthStatus string, err error) error {
	if err != nil {
		return fmt.Errorf("%s health check: status=%d health_status=%q: %w", checkName, status, healthStatus, err)
	}
	return fmt.Errorf("%s health check: status=%d health_status=%q", checkName, status, healthStatus)
}

func split(value string) []string {
	var out []string
	for _, item := range strings.Split(value, ",") {
		if item = strings.TrimSpace(item); item != "" {
			out = append(out, item)
		}
	}
	return out
}

func success(status int) bool { return status >= 200 && status < 300 }

func passed(name, category string, observed map[string]any) check {
	return check{Name: name, Category: category, Passed: true, Observed: observed}
}

func failed(name, category string, err error) check {
	return failedObserved(name, category, nil, err)
}

func failedObserved(name, category string, observed map[string]any, err error) check {
	message := ""
	if err != nil {
		message = err.Error()
	}
	return check{Name: name, Category: category, Passed: false, Observed: observed, Error: message}
}

func finalize(r report) report {
	r.Passed = len(r.Checks) > 0
	for _, item := range r.Checks {
		if item.Passed {
			r.Summary.Passed++
		} else {
			r.Summary.Failed++
			r.Passed = false
		}
	}
	return r
}

func writeReport(output string, r report) error {
	data, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if output == "" {
		_, err = os.Stdout.Write(data)
		return err
	}
	return os.WriteFile(output, data, 0o644)
}
