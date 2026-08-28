#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=(docker compose -f "$ROOT/environments/compose.yaml")
AMQP_ALL='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25673/,amqp://atlas:atlas-local-only@127.0.0.1:25674/'
MGMT_ALL='http://127.0.0.1:35672,http://127.0.0.1:35673,http://127.0.0.1:35674'
LIVE_EVIDENCE="$ROOT/evidence"
RUN_TOKEN="$(date -u +%Y%m%dT%H%M%SZ)-$$"
STAGING_EVIDENCE="$ROOT/.evidence-next-$RUN_TOKEN"
BACKUP_EVIDENCE="$ROOT/.evidence-previous-$RUN_TOKEN"
TRANSACTION_TMP=$(mktemp -d "${TMPDIR:-/tmp}/rabbitmq-evidence-transaction.XXXXXX")
TRANSACTION_STATE="$TRANSACTION_TMP/state.json"
TRANSACTION_ACTIVE=''
RAW="$STAGING_EVIDENCE/raw"
STOPPED_SERVICE=''
DISCONNECTED_CONTAINER=''
ALARM_ACTIVE=''

cleanup() {
  if [[ -n "$TRANSACTION_ACTIVE" && -f "$TRANSACTION_STATE" ]]; then
    python3 "$ROOT/scripts/evidence_transaction.py" rollback --state "$TRANSACTION_STATE" >/dev/null 2>&1 || true
  fi
  if [[ -n "$ALARM_ACTIVE" ]]; then "${COMPOSE[@]}" exec -T rabbitmq-1 rabbitmqctl set_vm_memory_high_watermark 0.4 >/dev/null 2>&1 || true; fi
  if [[ -n "$DISCONNECTED_CONTAINER" ]]; then docker network connect rabbitmq-reference-atlas-cluster "$DISCONNECTED_CONTAINER" >/dev/null 2>&1 || true; fi
  if [[ -n "$STOPPED_SERVICE" ]]; then "${COMPOSE[@]}" start "$STOPPED_SERVICE" >/dev/null 2>&1 || true; fi
  if [[ "${KEEP_ENV:-0}" != "1" ]]; then "${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true; fi
  rm -rf -- "$TRANSACTION_TMP"
}
trap cleanup EXIT

python3 "$ROOT/scripts/evidence_transaction.py" begin \
  --state "$TRANSACTION_STATE" \
  --live "$LIVE_EVIDENCE" \
  --staging "$STAGING_EVIDENCE" \
  --backup "$BACKUP_EVIDENCE" \
  --config "$ROOT/evidence-reporting.yaml" >/dev/null
TRANSACTION_ACTIVE=1
export RABBITMQ_EVIDENCE_ROOT="$STAGING_EVIDENCE"
export RABBITMQ_EVIDENCE_ONLY=1
export RABBITMQ_EVIDENCE_RUN_TOKEN="$RUN_TOKEN"
export RABBITMQ_EVIDENCE_OBSERVED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export RABBITMQ_EVIDENCE_RERUN_AT="$RABBITMQ_EVIDENCE_OBSERVED_AT"
mkdir -p "$RAW"
"${COMPOSE[@]}" up -d --wait

(cd "$ROOT" && go run ./cmd/rmq-amqp10-handshake --endpoints 127.0.0.1:25672,127.0.0.1:25673,127.0.0.1:25674 --output-dir "$RAW")
(cd "$ROOT" && python3 scripts/generate-amqp10-evidence.py)
(cd "$ROOT" && bash scripts/run-plugin-protocol-lab.sh)
(cd "$ROOT" && go run ./cmd/rmq-lab --mode cluster --management-urls "$MGMT_ALL" --output "$RAW/cluster-before.json")
(cd "$ROOT" && go run ./cmd/rmq-lab --mode core --amqp-urls "$AMQP_ALL" --output "$RAW/core.json")
(cd "$ROOT" && go run ./cmd/rmq-secops --amqp-urls "$AMQP_ALL" --management-urls "$MGMT_ALL" --output "$RAW/security-observability.json")
(cd "$ROOT" && bash scripts/run-observability-lab.sh "$RAW/observability-state.json" >/dev/null)
(cd "$ROOT" && go run ./cmd/rmq-benchmark --amqp-urls "$AMQP_ALL" --management-urls "$MGMT_ALL" --messages 300 --payload-bytes 1024 --output "$RAW/performance.json")
"${COMPOSE[@]}" exec -T rabbitmq-1 rabbitmqctl set_vm_memory_high_watermark absolute 1
ALARM_ACTIVE=1
(cd "$ROOT" && go run ./cmd/rmq-flow-control --amqp-url 'amqp://atlas:atlas-local-only@127.0.0.1:25672/' --management-url 'http://127.0.0.1:35672' --expect-blocked --output "$RAW/publisher-flow-blocked.json")
"${COMPOSE[@]}" exec -T rabbitmq-1 rabbitmqctl set_vm_memory_high_watermark 0.4
ALARM_ACTIVE=''
(cd "$ROOT" && go run ./cmd/rmq-flow-control --amqp-url 'amqp://atlas:atlas-local-only@127.0.0.1:25672/' --management-url 'http://127.0.0.1:35672' --output "$RAW/publisher-flow-resumed.json")
(cd "$ROOT" && go run ./cmd/rmq-lab --mode prepare-failure --amqp-urls "$AMQP_ALL" --management-urls "$MGMT_ALL" --expected 5 --output "$RAW/prepare-failure.json")

LEADER=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["leader"])' "$RAW/prepare-failure.json")
QUEUE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["queue"])' "$RAW/prepare-failure.json")
STOPPED_SERVICE=${LEADER#rabbit@}
case "$STOPPED_SERVICE" in rabbitmq-1|rabbitmq-2|rabbitmq-3) ;; *) echo "unexpected leader: $LEADER" >&2; exit 1 ;; esac
"${COMPOSE[@]}" stop "$STOPPED_SERVICE"

case "$STOPPED_SERVICE" in
  rabbitmq-1) LIVE_AMQP='amqp://atlas:atlas-local-only@127.0.0.1:25673/,amqp://atlas:atlas-local-only@127.0.0.1:25674/' ;;
  rabbitmq-2) LIVE_AMQP='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25674/' ;;
  rabbitmq-3) LIVE_AMQP='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25673/' ;;
esac
(cd "$ROOT" && go run ./cmd/rmq-lab --mode verify-recovery --amqp-urls "$LIVE_AMQP" --queue "$QUEUE" --expected 5 --output "$RAW/recovery.json")

"${COMPOSE[@]}" start "$STOPPED_SERVICE"
STOPPED_SERVICE=''
(cd "$ROOT" && go run ./cmd/rmq-lab --mode cluster --management-urls "$MGMT_ALL" --output "$RAW/cluster-after.json")

# Network partition is distinct from a stopped process. Isolate the current
# quorum leader on the dedicated Compose network, verify majority progress and
# minority rejection, then reconnect and wait for all replicas to be online.
(cd "$ROOT" && go run ./cmd/rmq-lab --mode prepare-failure --amqp-urls "$AMQP_ALL" --management-urls "$MGMT_ALL" --expected 5 --output "$RAW/partition-prepare.json")
PARTITION_LEADER=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["leader"])' "$RAW/partition-prepare.json")
PARTITION_QUEUE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["queue"])' "$RAW/partition-prepare.json")
PARTITION_SERVICE=${PARTITION_LEADER#rabbit@}
case "$PARTITION_SERVICE" in
  rabbitmq-1) PARTITION_MINORITY='amqp://atlas:atlas-local-only@127.0.0.1:25672/'; PARTITION_MAJORITY='amqp://atlas:atlas-local-only@127.0.0.1:25673/,amqp://atlas:atlas-local-only@127.0.0.1:25674/' ;;
  rabbitmq-2) PARTITION_MINORITY='amqp://atlas:atlas-local-only@127.0.0.1:25673/'; PARTITION_MAJORITY='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25674/' ;;
  rabbitmq-3) PARTITION_MINORITY='amqp://atlas:atlas-local-only@127.0.0.1:25674/'; PARTITION_MAJORITY='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25673/' ;;
  *) echo "unexpected partition leader: $PARTITION_LEADER" >&2; exit 1 ;;
esac
DISCONNECTED_CONTAINER=$("${COMPOSE[@]}" ps -q "$PARTITION_SERVICE")
test -n "$DISCONNECTED_CONTAINER"
docker network disconnect --force rabbitmq-reference-atlas-cluster "$DISCONNECTED_CONTAINER"
(cd "$ROOT" && go run ./cmd/rmq-lab --mode verify-minority --amqp-urls "$PARTITION_MINORITY" --queue "$PARTITION_QUEUE" --output "$RAW/partition-minority.json")
# RabbitMQ/ErlangのNode Down検出を待ち、Election前に閉じられたChannelを
# 多数派検証へ持ち込まない。
sleep 30
(cd "$ROOT" && go run ./cmd/rmq-lab --mode verify-partition-majority --amqp-urls "$PARTITION_MAJORITY" --queue "$PARTITION_QUEUE" --expected 5 --output "$RAW/partition-majority.json")
docker network connect rabbitmq-reference-atlas-cluster "$DISCONNECTED_CONTAINER"
DISCONNECTED_CONTAINER=''
(cd "$ROOT" && go run ./cmd/rmq-lab --mode inspect-queue --amqp-urls "$AMQP_ALL" --management-urls "$MGMT_ALL" --queue "$PARTITION_QUEUE" --output "$RAW/partition-recovery.json")
(cd "$ROOT" && go run ./cmd/rmq-lab --mode cluster --management-urls "$MGMT_ALL" --output "$RAW/cluster-after-partition.json")
(cd "$ROOT" && bash scripts/run-tls-lab.sh "$RAW/security-tls.json")
(cd "$ROOT" && bash scripts/run-upgrade-migration-lab.sh "$RAW/upgrade-migration.json")
(cd "$ROOT" && python3 scripts/run-skill-evals.py >/dev/null)
(cd "$ROOT" && python3 scripts/generate-evidence.py)
(cd "$ROOT" && python3 scripts/generate-scenario-proofs.py)
(cd "$ROOT" && python3 scripts/evidence_dependency_graph.py generate)
ATLAS_STATUS=$(cd "$ROOT" && python3 -c 'import yaml; print(yaml.safe_load(open("atlas.yaml"))["status"])')
if [[ "$ATLAS_STATUS" == "complete" ]]; then
  (cd "$ROOT" && python3 scripts/generate-completion-certificate.py)
fi

python3 "$ROOT/scripts/evidence_transaction.py" verify --state "$TRANSACTION_STATE" >/dev/null
python3 "$ROOT/scripts/evidence_transaction.py" swap --state "$TRANSACTION_STATE" >/dev/null
if [[ "$ATLAS_STATUS" == "complete" ]]; then
  (cd "$ROOT" && python3 scripts/validate-repository.py --release)
else
  (cd "$ROOT" && python3 scripts/validate-repository.py)
fi
python3 "$ROOT/scripts/evidence_transaction.py" finalize --state "$TRANSACTION_STATE" >/dev/null
TRANSACTION_ACTIVE=''

echo "RabbitMQ Labsのfull-run passを確認し、Evidence集合を原子的に公開しました。"
