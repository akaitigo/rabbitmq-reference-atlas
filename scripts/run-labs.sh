#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=(docker compose -f "$ROOT/environments/compose.yaml")
SECURITY_COMPOSE=(docker compose -f "$ROOT/environments/security-001.compose.yaml")
AMQP_ALL='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25673/,amqp://atlas:atlas-local-only@127.0.0.1:25674/'
MGMT_ALL='http://127.0.0.1:35672,http://127.0.0.1:35673,http://127.0.0.1:35674'
LIVE_EVIDENCE="$ROOT/evidence"
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_TOKEN="${RUN_STARTED_AT//[-:]/}-$$"
STAGING_EVIDENCE="$ROOT/.evidence-next-$RUN_TOKEN"
BACKUP_EVIDENCE="$ROOT/.evidence-previous-$RUN_TOKEN"
TRANSACTION_TMP=$(mktemp -d "${TMPDIR:-/tmp}/rabbitmq-evidence-transaction.XXXXXX")
TRANSACTION_STATE="$TRANSACTION_TMP/state.json"
EVAL_BACKUP="$TRANSACTION_TMP/evals"
TRANSACTION_ACTIVE=''
EVALS_UPDATED=''
RAW="$STAGING_EVIDENCE/raw"
STOPPED_SERVICE=''
DISCONNECTED_CONTAINER=''
ALARM_ACTIVE=''
SECURITY_ENV_ACTIVE=''

cleanup() {
  if [[ -n "$TRANSACTION_ACTIVE" && -f "$TRANSACTION_STATE" ]]; then
    python3 "$ROOT/scripts/evidence_transaction.py" rollback --state "$TRANSACTION_STATE" >/dev/null 2>&1 || true
  fi
  if [[ -n "$EVALS_UPDATED" && -d "$EVAL_BACKUP" ]]; then
    cp "$EVAL_BACKUP/rabbitmq-reference-atlas.skill-routing-eval.json" "$ROOT/evals/rabbitmq-reference-atlas.skill-routing-eval.json" >/dev/null 2>&1 || true
    cp "$EVAL_BACKUP/rabbitmq-reference-atlas.definitive-skill-eval.json" "$ROOT/evals/rabbitmq-reference-atlas.definitive-skill-eval.json" >/dev/null 2>&1 || true
  fi
  if [[ -n "$ALARM_ACTIVE" ]]; then "${COMPOSE[@]}" exec -T rabbitmq-1 rabbitmqctl set_vm_memory_high_watermark 0.4 >/dev/null 2>&1 || true; fi
  if [[ -n "$SECURITY_ENV_ACTIVE" ]]; then "${SECURITY_COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true; fi
  if [[ -n "$DISCONNECTED_CONTAINER" ]]; then docker network connect rabbitmq-reference-atlas-cluster "$DISCONNECTED_CONTAINER" >/dev/null 2>&1 || true; fi
  if [[ -n "$STOPPED_SERVICE" ]]; then "${COMPOSE[@]}" start "$STOPPED_SERVICE" >/dev/null 2>&1 || true; fi
  if [[ -n "${RABBITMQ_SCENARIO_METADATA_VHOST:-}" ]]; then
    for service in rabbitmq-1 rabbitmq-2 rabbitmq-3; do
      "${COMPOSE[@]}" exec -T "$service" rabbitmqctl delete_vhost "$RABBITMQ_SCENARIO_METADATA_VHOST" >/dev/null 2>&1 && break || true
    done
  fi
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
mkdir -p "$EVAL_BACKUP"
cp "$ROOT/evals/rabbitmq-reference-atlas.skill-routing-eval.json" "$EVAL_BACKUP/"
cp "$ROOT/evals/rabbitmq-reference-atlas.definitive-skill-eval.json" "$EVAL_BACKUP/"
export RABBITMQ_EVIDENCE_ROOT="$STAGING_EVIDENCE"
export RABBITMQ_EVIDENCE_ONLY=1
export RABBITMQ_EVIDENCE_RUN_TOKEN="$RUN_TOKEN"
export RABBITMQ_EVIDENCE_OBSERVED_AT="$RUN_STARTED_AT"
export RABBITMQ_EVIDENCE_RERUN_AT="$RABBITMQ_EVIDENCE_OBSERVED_AT"
export RABBITMQ_EVIDENCE_PREVIOUS_GRAPH="$LIVE_EVIDENCE/dependency-graph.json"
export RABBITMQ_SCENARIO_METADATA_VHOST="/atlas-metadata-$RUN_TOKEN"
mkdir -p "$RAW"
"${COMPOSE[@]}" up -d --wait

# ping healthcheckはErlang nodeの起動を示す。実Protocol Scenarioは各nodeの
# AMQP listenerと3-node membershipが揃った後に一度だけ開始する。
for service in rabbitmq-1 rabbitmq-2 rabbitmq-3; do
  LISTENER_READY=''
  for _ in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T "$service" rabbitmq-diagnostics listeners 2>/dev/null | grep -q 'port: 5672'; then
      LISTENER_READY=1
      break
    fi
    sleep 1
  done
  if [[ -z "$LISTENER_READY" ]]; then
    "${COMPOSE[@]}" logs --no-color --tail 200 "$service" >&2
    echo "$service のAMQP listener 5672がreadiness期限内に起動しませんでした。" >&2
    exit 1
  fi
done
"${COMPOSE[@]}" exec -T rabbitmq-1 rabbitmqctl await_online_nodes 3

(cd "$ROOT" && go run ./cmd/rmq-amqp10-handshake --endpoints 127.0.0.1:25672,127.0.0.1:25673,127.0.0.1:25674 --output-dir "$RAW")
(cd "$ROOT" && python3 scripts/generate-amqp10-evidence.py)
(cd "$ROOT" && bash scripts/run-plugin-protocol-lab.sh)
(cd "$ROOT" && python3 scripts/generate-scenario-runtime.py protocols)
(cd "$ROOT" && go run ./cmd/rmq-lab --mode cluster --management-urls "$MGMT_ALL" --output "$RAW/cluster-before.json")
(cd "$ROOT" && go run ./cmd/rmq-lab --mode core --amqp-urls "$AMQP_ALL" --output "$RAW/core.json")
(cd "$ROOT" && go run ./cmd/rmq-secops --amqp-urls "$AMQP_ALL" --management-urls "$MGMT_ALL" --output "$RAW/security-observability.json")
(cd "$ROOT" && python3 scripts/generate-scenario-runtime.py steady-state-tranche)

# security-001はmain clusterとは別のCompose project/network/ports/tmpfsを使う。
# OpenLDAPとLDAP auth backendを有効にした3-node実Brokerを起動し、認証と
# authorizationを専用Client/Oracleで一度だけ駆動する。
"${SECURITY_COMPOSE[@]}" up -d --wait
SECURITY_ENV_ACTIVE=1
for service in security-rabbitmq-1 security-rabbitmq-2 security-rabbitmq-3; do
  SECURITY_LISTENER_READY=''
  for _ in $(seq 1 60); do
    if "${SECURITY_COMPOSE[@]}" exec -T "$service" rabbitmq-diagnostics listeners 2>/dev/null | grep -q 'port: 5672'; then
      SECURITY_LISTENER_READY=1
      break
    fi
    sleep 1
  done
  if [[ -z "$SECURITY_LISTENER_READY" ]]; then
    "${SECURITY_COMPOSE[@]}" logs --no-color --tail 200 "$service" >&2
    echo "$service のLDAP security AMQP listenerがreadiness期限内に起動しませんでした。" >&2
    exit 1
  fi
done
"${SECURITY_COMPOSE[@]}" exec -T security-rabbitmq-1 rabbitmqctl await_online_nodes 3
for service in security-rabbitmq-1 security-rabbitmq-2 security-rabbitmq-3; do
  "${SECURITY_COMPOSE[@]}" exec -T "$service" rabbitmq-plugins list -e -m | grep -qx rabbitmq_auth_backend_ldap
done
(cd "$ROOT" && python3 scripts/generate-scenario-runtime.py security-001-ldap)
"${SECURITY_COMPOSE[@]}" down --remove-orphans
SECURITY_ENV_ACTIVE=''

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
(cd "$ROOT" && python3 scripts/generate-scenario-runtime.py node-failure --stopped-service "$STOPPED_SERVICE")

case "$STOPPED_SERVICE" in
  rabbitmq-1) LIVE_AMQP='amqp://atlas:atlas-local-only@127.0.0.1:25673/,amqp://atlas:atlas-local-only@127.0.0.1:25674/' ;;
  rabbitmq-2) LIVE_AMQP='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25674/' ;;
  rabbitmq-3) LIVE_AMQP='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25673/' ;;
esac
(cd "$ROOT" && go run ./cmd/rmq-lab --mode verify-recovery --amqp-urls "$LIVE_AMQP" --queue "$QUEUE" --expected 5 --output "$RAW/recovery.json")

"${COMPOSE[@]}" start "$STOPPED_SERVICE"
STOPPED_SERVICE=''
(cd "$ROOT" && go run ./cmd/rmq-lab --mode cluster --management-urls "$MGMT_ALL" --output "$RAW/cluster-after.json")
(cd "$ROOT" && python3 scripts/generate-scenario-runtime.py node-recovery)

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
(cd "$ROOT" && python3 scripts/generate-scenario-runtime.py partition-failure --queue "$PARTITION_QUEUE" --isolated-service "$PARTITION_SERVICE")
docker network connect rabbitmq-reference-atlas-cluster "$DISCONNECTED_CONTAINER"
DISCONNECTED_CONTAINER=''
(cd "$ROOT" && go run ./cmd/rmq-lab --mode inspect-queue --amqp-urls "$AMQP_ALL" --management-urls "$MGMT_ALL" --queue "$PARTITION_QUEUE" --output "$RAW/partition-recovery.json")
(cd "$ROOT" && go run ./cmd/rmq-lab --mode cluster --management-urls "$MGMT_ALL" --output "$RAW/cluster-after-partition.json")
(cd "$ROOT" && python3 scripts/generate-scenario-runtime.py partition-recovery --queue "$PARTITION_QUEUE")
(cd "$ROOT" && bash scripts/run-tls-lab.sh "$RAW/security-tls.json")
(cd "$ROOT" && bash scripts/run-upgrade-migration-lab.sh "$RAW/upgrade-migration.json")
(cd "$ROOT" && python3 scripts/run-skill-evals.py >/dev/null)
(cd "$ROOT" && python3 scripts/generate-evidence.py)
EVALS_UPDATED=1
(cd "$ROOT" && python3 scripts/run-definitive-skill-eval.py)
(cd "$ROOT" && python3 scripts/generate-scenario-proofs.py)
(cd "$ROOT" && python3 scripts/evidence_dependency_graph.py generate)
ATLAS_STATUS=$(cd "$ROOT" && python3 -c 'import yaml; print(yaml.safe_load(open("atlas.yaml"))["status"])')
if [[ "$ATLAS_STATUS" == "complete" ]]; then
  (cd "$ROOT" && python3 scripts/generate-completion-certificate.py)
fi

python3 "$ROOT/scripts/evidence_transaction.py" verify --state "$TRANSACTION_STATE" >/dev/null
python3 "$ROOT/scripts/evidence_transaction.py" swap --state "$TRANSACTION_STATE" >/dev/null
# staging directoryはliveへrename済みなので、post-swap Gateは公開先を読む。
export RABBITMQ_EVIDENCE_ROOT="$LIVE_EVIDENCE"
if [[ "$ATLAS_STATUS" == "complete" ]]; then
  (cd "$ROOT" && python3 scripts/validate-repository.py --release)
else
  (cd "$ROOT" && python3 scripts/validate-repository.py)
fi
python3 "$ROOT/scripts/evidence_transaction.py" finalize --state "$TRANSACTION_STATE" >/dev/null
TRANSACTION_ACTIVE=''
EVALS_UPDATED=''

echo "RabbitMQ Labsのfull-run passを確認し、Evidence集合を原子的に公開しました。"
