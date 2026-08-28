#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
UPGRADE_PROJECT="rabbitmq-reference-atlas-upgrade-${RABBITMQ_EVIDENCE_RUN_TOKEN:-$(date -u +%s)-$$}"
UPGRADE_PROJECT=$(printf '%s' "$UPGRADE_PROJECT" | tr '[:upper:]' '[:lower:]')
COMPOSE=(docker compose -p "$UPGRADE_PROJECT" -f "$ROOT/environments/upgrade.compose.yaml")
SOURCE_IMAGE='rabbitmq:4.2.9-management@sha256:59935db6392a27b5192f1be080df9b4194bc22f104a7a1bf3b31479a8e0d1031'
TARGET_IMAGE='rabbitmq:4.3.5-management@sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031'
AMQP_URLS='amqp://atlas:atlas-local-only@127.0.0.1:27672/,amqp://atlas:atlas-local-only@127.0.0.1:27673/,amqp://atlas:atlas-local-only@127.0.0.1:27674/'
MANAGEMENT_URL='http://127.0.0.1:37672'
OUTPUT="${1:-${RABBITMQ_EVIDENCE_ROOT:-$ROOT/evidence}/raw/upgrade-migration.json}"
UPGRADE_TMP=$(mktemp -d "${TMPDIR:-/tmp}/rabbitmq-atlas-upgrade.XXXXXX")
PRESTOP="$UPGRADE_TMP/prestop"
WORKLOAD_PID=''

export UPGRADE_IMAGE_1="$SOURCE_IMAGE"
export UPGRADE_IMAGE_2="$SOURCE_IMAGE"
export UPGRADE_IMAGE_3="$SOURCE_IMAGE"

cleanup() {
  if [[ -n "$WORKLOAD_PID" ]] && kill -0 "$WORKLOAD_PID" >/dev/null 2>&1; then
    kill "$WORKLOAD_PID" >/dev/null 2>&1 || true
    wait "$WORKLOAD_PID" >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_UPGRADE_ENV:-0}" != "1" ]]; then
    "${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -d "$UPGRADE_TMP" ]]; then
    rm -rf -- "$UPGRADE_TMP"
  fi
}
trap cleanup EXIT

mkdir -p "$PRESTOP" "$(dirname "$OUTPUT")"
"${COMPOSE[@]}" up -d --wait

wait_rabbit_running() {
  local service=$1
  local running=''
  for _ in {1..60}; do
    if "${COMPOSE[@]}" exec -T "$service" rabbitmq-diagnostics -q check_running >/dev/null 2>&1; then
      running=1
      break
    fi
    sleep 2
  done
  if [[ -z "$running" ]]; then
    echo "$service のRabbit applicationが120秒以内にrunningになりませんでした。" >&2
    return 1
  fi
}

for service in upgrade-1 upgrade-2 upgrade-3; do
  wait_rabbit_running "$service"
done
"${COMPOSE[@]}" exec -T upgrade-1 rabbitmqctl await_online_nodes 3

"${COMPOSE[@]}" exec -T upgrade-1 rabbitmqctl enable_feature_flag all
"${COMPOSE[@]}" exec -T upgrade-1 rabbitmqctl list_feature_flags >"$UPGRADE_TMP/feature-flags-before.txt"

printf '%s\n' source >"$UPGRADE_TMP/phase"
(
  cd "$ROOT"
  GOMODCACHE="$ROOT/.cache/go-mod" GOCACHE="$ROOT/.cache/go-build" go run ./cmd/rmq-upgrade-workload \
    --amqp-urls "$AMQP_URLS" \
    --phase-file "$UPGRADE_TMP/phase" \
    --stop-file "$UPGRADE_TMP/stop" \
    --ready-file "$UPGRADE_TMP/ready" \
    --output "$UPGRADE_TMP/workload.json"
) >"$UPGRADE_TMP/workload.stdout.log" 2>"$UPGRADE_TMP/workload.stderr.log" &
WORKLOAD_PID=$!

ready_deadline=$((SECONDS + 120))
while [[ ! -f "$UPGRADE_TMP/ready" ]]; do
  if ! kill -0 "$WORKLOAD_PID" >/dev/null 2>&1; then
    wait "$WORKLOAD_PID"
  fi
  if (( SECONDS >= ready_deadline )); then
    echo "Upgrade Workloadの準備が120秒以内に完了しませんでした。" >&2
    exit 1
  fi
  sleep 1
done

capture_snapshot() {
  local phase=$1
  shift
  local output="$UPGRADE_TMP/${phase}.json"
  local deadline=$((SECONDS + 120))
  while ! python3 "$ROOT/scripts/capture-upgrade-snapshot.py" \
    --phase "$phase" \
    --management-url "$MANAGEMENT_URL" \
    "$@" \
    --output "$output" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      python3 "$ROOT/scripts/capture-upgrade-snapshot.py" \
        --phase "$phase" --management-url "$MANAGEMENT_URL" "$@" --output "$output"
      return 1
    fi
    sleep 1
  done
}

sleep 2
capture_snapshot source \
  --expected rabbit@upgrade-1=4.2.9 \
  --expected rabbit@upgrade-2=4.2.9 \
  --expected rabbit@upgrade-3=4.2.9

upgrade_node() {
  local service=$1
  local image_variable=$2
  local transition_phase=$3
  local stable_phase=$4
  printf '%s\n' "$transition_phase" >"$UPGRADE_TMP/phase"
  "${COMPOSE[@]}" exec -T "$service" rabbitmq-diagnostics check_if_node_is_quorum_critical >"$PRESTOP/${service}-quorum-critical.log" 2>&1
  "${COMPOSE[@]}" exec -T "$service" rabbitmq-diagnostics check_if_new_quorum_queue_replicas_have_finished_initial_sync >"$PRESTOP/${service}-initial-sync.log" 2>&1
  "${COMPOSE[@]}" exec -T "$service" rabbitmq-upgrade await_online_quorum_plus_one >"$PRESTOP/${service}-online-quorum.log" 2>&1
  "${COMPOSE[@]}" exec -T "$service" rabbitmq-upgrade drain >"$PRESTOP/${service}-drain.log" 2>&1
  export "$image_variable=$TARGET_IMAGE"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate --wait "$service"
  wait_rabbit_running "$service"
  printf '%s\n' "$stable_phase" >"$UPGRADE_TMP/phase"
  sleep 2
}

upgrade_node upgrade-3 UPGRADE_IMAGE_3 upgrading-3 mixed-1
capture_snapshot mixed-1 \
  --expected rabbit@upgrade-1=4.2.9 \
  --expected rabbit@upgrade-2=4.2.9 \
  --expected rabbit@upgrade-3=4.3.5

upgrade_node upgrade-2 UPGRADE_IMAGE_2 upgrading-2 mixed-2
capture_snapshot mixed-2 \
  --expected rabbit@upgrade-1=4.2.9 \
  --expected rabbit@upgrade-2=4.3.5 \
  --expected rabbit@upgrade-3=4.3.5

upgrade_node upgrade-1 UPGRADE_IMAGE_1 upgrading-1 target
"${COMPOSE[@]}" exec -T upgrade-1 rabbitmqctl enable_feature_flag all
"${COMPOSE[@]}" exec -T upgrade-1 rabbitmq-queues rebalance all
"${COMPOSE[@]}" exec -T upgrade-1 rabbitmqctl list_feature_flags >"$UPGRADE_TMP/feature-flags-after.txt"
sleep 2
capture_snapshot target \
  --expected rabbit@upgrade-1=4.3.5 \
  --expected rabbit@upgrade-2=4.3.5 \
  --expected rabbit@upgrade-3=4.3.5

touch "$UPGRADE_TMP/stop"
wait "$WORKLOAD_PID"
WORKLOAD_PID=''

python3 "$ROOT/scripts/assemble-upgrade-evidence.py" \
  --snapshot "$UPGRADE_TMP/source.json" \
  --snapshot "$UPGRADE_TMP/mixed-1.json" \
  --snapshot "$UPGRADE_TMP/mixed-2.json" \
  --snapshot "$UPGRADE_TMP/target.json" \
  --workload "$UPGRADE_TMP/workload.json" \
  --prestop-dir "$PRESTOP" \
  --feature-flags-before "$UPGRADE_TMP/feature-flags-before.txt" \
  --feature-flags-after "$UPGRADE_TMP/feature-flags-after.txt" \
  --output "$OUTPUT"

echo "RabbitMQ 4.2.9から4.3.5へのRolling Upgrade Evidenceを生成しました: $OUTPUT"
