#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=(docker compose -f "$ROOT/environments/compose.yaml")
AMQP_ALL='amqp://atlas:atlas-local-only@127.0.0.1:25672/,amqp://atlas:atlas-local-only@127.0.0.1:25673/,amqp://atlas:atlas-local-only@127.0.0.1:25674/'
MGMT_ALL='http://127.0.0.1:35672,http://127.0.0.1:35673,http://127.0.0.1:35674'
RAW="$ROOT/evidence/raw"
STOPPED_SERVICE=''

cleanup() {
  if [[ -n "$STOPPED_SERVICE" ]]; then "${COMPOSE[@]}" start "$STOPPED_SERVICE" >/dev/null 2>&1 || true; fi
  if [[ "${KEEP_ENV:-0}" != "1" ]]; then "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

mkdir -p "$RAW"
"${COMPOSE[@]}" up -d --wait

(cd "$ROOT" && go run ./cmd/rmq-lab --mode cluster --management-urls "$MGMT_ALL" --output "$RAW/cluster-before.json")
(cd "$ROOT" && go run ./cmd/rmq-lab --mode core --amqp-urls "$AMQP_ALL" --output "$RAW/core.json")
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
(cd "$ROOT" && python3 scripts/run-skill-evals.py >/dev/null)
(cd "$ROOT" && python3 scripts/generate-evidence.py)
(cd "$ROOT" && python3 scripts/update-coverage-evidence.py)
(cd "$ROOT" && python3 scripts/sync-authority-digest.py >/dev/null)
(cd "$ROOT" && python3 scripts/generate-evidence.py)
(cd "$ROOT" && python3 scripts/validate-repository.py)

echo "RabbitMQ LabsとEvidence生成が完了しました。"

