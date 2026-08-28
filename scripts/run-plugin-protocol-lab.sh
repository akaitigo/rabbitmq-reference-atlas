#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=(docker compose -f "$ROOT/environments/compose.yaml")
SERVICES=(rabbitmq-1 rabbitmq-2 rabbitmq-3)
PLUGINS=(rabbitmq_mqtt rabbitmq_stomp rabbitmq_stream)
RAW="$ROOT/evidence/raw"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/rabbitmq-plugin-protocols.XXXXXX")
ORIGINAL_1=''
ORIGINAL_2=''
ORIGINAL_3=''

original_for() {
  case "$1" in
    rabbitmq-1) printf '%s' "$ORIGINAL_1" ;;
    rabbitmq-2) printf '%s' "$ORIGINAL_2" ;;
    rabbitmq-3) printf '%s' "$ORIGINAL_3" ;;
  esac
}

restore() {
  for service in "${SERVICES[@]}"; do
    original=$(original_for "$service")
    for plugin in "${PLUGINS[@]}"; do
      if [[ " $original " != *" $plugin "* ]]; then
        "${COMPOSE[@]}" exec -T "$service" rabbitmq-plugins disable "$plugin" >/dev/null 2>&1 || true
      fi
    done
  done
  rm -rf "$TMP"
}
trap restore EXIT

mkdir -p "$RAW"
for service in "${SERVICES[@]}"; do
  original=$("${COMPOSE[@]}" exec -T "$service" rabbitmq-plugins list -E -m | tr '\n' ' ')
  case "$service" in
    rabbitmq-1) ORIGINAL_1=$original ;;
    rabbitmq-2) ORIGINAL_2=$original ;;
    rabbitmq-3) ORIGINAL_3=$original ;;
  esac
  "${COMPOSE[@]}" exec -T "$service" rabbitmq-plugins enable "${PLUGINS[@]}" >/dev/null
done

for service in "${SERVICES[@]}"; do
  for protocol in mqtt stomp stream; do
    for _ in $(seq 1 30); do
      if "${COMPOSE[@]}" exec -T "$service" rabbitmq-diagnostics listeners | grep -q "protocol: $protocol"; then break; fi
      sleep 1
    done
    "${COMPOSE[@]}" exec -T "$service" rabbitmq-diagnostics listeners | grep -q "protocol: $protocol"
  done
done

CONTAINER_ARCH=$("${COMPOSE[@]}" exec -T rabbitmq-1 uname -m | tr -d '\r')
case "$CONTAINER_ARCH" in
  aarch64|arm64) GOARCH_TARGET=arm64 ;;
  x86_64|amd64) GOARCH_TARGET=amd64 ;;
  *) echo "unsupported RabbitMQ container architecture: $CONTAINER_ARCH" >&2; exit 1 ;;
esac
(cd "$ROOT" && env CGO_ENABLED=0 GOOS=linux GOARCH="$GOARCH_TARGET" go build -o "$TMP/rmq-plugin-protocols" ./cmd/rmq-plugin-protocols)
CONTAINER=$("${COMPOSE[@]}" ps -q rabbitmq-1)
test -n "$CONTAINER"
docker cp "$TMP/rmq-plugin-protocols" "$CONTAINER:/tmp/rmq-plugin-protocols"
"${COMPOSE[@]}" exec -T rabbitmq-1 /tmp/rmq-plugin-protocols --output-dir /tmp/rabbitmq-plugin-protocols
docker cp "$CONTAINER:/tmp/rabbitmq-plugin-protocols/." "$RAW"
(cd "$ROOT" && python3 scripts/generate-plugin-protocol-evidence.py)

echo "MQTT/STOMP三Node protocol Evidenceを生成し、Plugin状態を実行前へ戻しました。"
