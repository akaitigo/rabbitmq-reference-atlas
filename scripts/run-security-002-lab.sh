#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
LDAP_COMPOSE=(docker compose -f "$ROOT/environments/security-002-ldap.compose.yaml")
OAUTH_COMPOSE=(docker compose -f "$ROOT/environments/security-002-oauth.compose.yaml")
EVIDENCE_ROOT=${RABBITMQ_EVIDENCE_ROOT:-$ROOT/evidence}
RAW="$EVIDENCE_ROOT/raw"
RUN_TOKEN=${RABBITMQ_EVIDENCE_RUN_TOKEN:?RABBITMQ_EVIDENCE_RUN_TOKEN is required}
CERT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/rabbitmq-security-002-certs.XXXXXX")
LDAP_ACTIVE=''
OAUTH_ACTIVE=''

cleanup() {
  if [[ -n "$LDAP_ACTIVE" ]]; then "${LDAP_COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true; fi
  if [[ -n "$OAUTH_ACTIVE" ]]; then "${OAUTH_COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true; fi
  chmod -R u+w "$CERT_DIR" >/dev/null 2>&1 || true
  rm -rf -- "$CERT_DIR"
}

failure_logs() {
  local phase=$1
  echo "security-002 ${phase} runtime failed; task-owned compose state follows" >&2
  if [[ "$phase" == ldap ]]; then
    "${LDAP_COMPOSE[@]}" ps --all >&2 || true
    "${LDAP_COMPOSE[@]}" logs --no-color --tail 240 >&2 || true
  else
    "${OAUTH_COMPOSE[@]}" ps --all >&2 || true
    "${OAUTH_COMPOSE[@]}" logs --no-color --tail 240 >&2 || true
  fi
}

trap cleanup EXIT

MIN_FREE=$(python3 -c 'import yaml; print(yaml.safe_load(open("runtime/security-002.contract.yaml"))["resource_policy"]["minimum_free_bytes_before_runtime"])')
AVAILABLE_KIB=$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')
AVAILABLE_BYTES=$((AVAILABLE_KIB * 1024))
if (( AVAILABLE_BYTES < MIN_FREE )); then
  echo "security-002 runtime skipped: free bytes $AVAILABLE_BYTES is below required $MIN_FREE" >&2
  exit 75
fi

mkdir -p "$RAW"
chmod 0755 "$CERT_DIR"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj '/CN=rabbitmq-security-002-ca' \
  -keyout "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt" >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -subj '/CN=ldap-directory' \
  -keyout "$CERT_DIR/ldap.key" -out "$CERT_DIR/ldap.csr" >/dev/null 2>&1
openssl x509 -req -days 2 -in "$CERT_DIR/ldap.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
  -CAcreateserial -extfile <(printf 'subjectAltName=DNS:ldap-directory,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n') \
  -out "$CERT_DIR/ldap.crt" >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj '/CN=rabbitmq-security-002-wrong-ca' \
  -keyout "$CERT_DIR/wrong-ca.key" -out "$CERT_DIR/wrong-ca.crt" >/dev/null 2>&1
chmod 0644 "$CERT_DIR/ca.crt" "$CERT_DIR/ldap.crt" "$CERT_DIR/wrong-ca.crt"
# The pinned OpenLDAP image runs as an unprivileged container UID. The key is
# created in a unique task-owned temporary directory, mounted read-only, and
# destroyed by the EXIT trap.
chmod 0644 "$CERT_DIR/ldap.key"
export SECURITY_002_CERT_DIR="$CERT_DIR"

"${LDAP_COMPOSE[@]}" config --quiet
LDAP_ACTIVE=1
if ! "${LDAP_COMPOSE[@]}" up -d --wait; then failure_logs ldap; exit 1; fi
"${LDAP_COMPOSE[@]}" exec -T security-rabbitmq-1 rabbitmqctl await_online_nodes 3
for service in security-rabbitmq-1 security-rabbitmq-2 security-rabbitmq-3; do
  "${LDAP_COMPOSE[@]}" exec -T "$service" rabbitmq-plugins list -e -m | grep -qx rabbitmq_auth_backend_ldap
done

VHOSTS=("atlas-security-002-${RUN_TOKEN}-1" "atlas-security-002-${RUN_TOKEN}-2" "atlas-security-002-${RUN_TOKEN}-3")
for vhost in "${VHOSTS[@]}"; do
  "${LDAP_COMPOSE[@]}" exec -T security-rabbitmq-1 rabbitmqctl add_vhost "$vhost"
  "${LDAP_COMPOSE[@]}" exec -T security-rabbitmq-1 rabbitmqctl set_vhost_limits -p "$vhost" '{"max-queues":1}'
done
VHOST_CSV=$(IFS=,; echo "${VHOSTS[*]}")
export RABBITMQ_LDAP_ALLOWED_USER=atlas-allowed
export RABBITMQ_LDAP_ALLOWED_PASSWORD=atlas-allowed-local-only
export RABBITMQ_LDAP_BAD_PASSWORD=atlas-invalid-local-only
(cd "$ROOT" && go run ./cmd/rmq-security-002 --vhosts "$VHOST_CSV" > "$RAW/security-002-ldap-limits.json")
(cd "$ROOT" && python3 scripts/generate-security-002-runtime.py ldap-limits)
for vhost in "${VHOSTS[@]}"; do
  "${LDAP_COMPOSE[@]}" exec -T security-rabbitmq-1 rabbitmqctl delete_vhost "$vhost"
done
"${LDAP_COMPOSE[@]}" down --volumes --remove-orphans
LDAP_ACTIVE=''

"${OAUTH_COMPOSE[@]}" config --quiet
OAUTH_ACTIVE=1
if ! "${OAUTH_COMPOSE[@]}" up -d --wait; then failure_logs oauth; exit 1; fi
"${OAUTH_COMPOSE[@]}" exec -T oauth-rabbitmq-1 rabbitmqctl await_online_nodes 3
for service in oauth-rabbitmq-1 oauth-rabbitmq-2 oauth-rabbitmq-3; do
  "${OAUTH_COMPOSE[@]}" exec -T "$service" rabbitmq-plugins list -e -m | grep -qx rabbitmq_auth_backend_oauth2
done
(cd "$ROOT" && python3 scripts/generate-security-002-runtime.py oauth)
"${OAUTH_COMPOSE[@]}" down --volumes --remove-orphans
OAUTH_ACTIVE=''

echo "security-002 actual LDAP TLS, queue limit and Keycloak OAuth runtime passed"
