#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TLS_RUNTIME=$(mktemp -d "${TMPDIR:-/tmp}/rabbitmq-reference-atlas-tls.XXXXXX")
export RABBITMQ_ATLAS_TLS_DIR="$TLS_RUNTIME"
COMPOSE=(docker compose -f "$ROOT/environments/tls.compose.yaml")
OUTPUT="${1:-${RABBITMQ_EVIDENCE_ROOT:-$ROOT/evidence}/raw/security-tls.json}"

cleanup() {
  "${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$TLS_RUNTIME"
}
trap cleanup EXIT

chmod 700 "$TLS_RUNTIME"

openssl req -new -newkey rsa:2048 -sha256 -nodes \
  -keyout "$TLS_RUNTIME/ca-key.pem" \
  -out "$TLS_RUNTIME/ca.csr" \
  -subj '/CN=RabbitMQ Reference Atlas Ephemeral CA' >/dev/null 2>&1
printf '%s\n' \
  'basicConstraints=critical,CA:TRUE' \
  'keyUsage=critical,keyCertSign,cRLSign' \
  'subjectKeyIdentifier=hash' >"$TLS_RUNTIME/ca.ext"
openssl x509 -req -sha256 -days 2 \
  -in "$TLS_RUNTIME/ca.csr" \
  -signkey "$TLS_RUNTIME/ca-key.pem" \
  -extfile "$TLS_RUNTIME/ca.ext" \
  -out "$TLS_RUNTIME/ca-cert.pem" >/dev/null 2>&1

openssl req -new -newkey rsa:2048 -sha256 -nodes \
  -keyout "$TLS_RUNTIME/server-key.pem" \
  -out "$TLS_RUNTIME/server.csr" \
  -subj '/CN=rabbitmq-tls' >/dev/null 2>&1
printf '%s\n' \
  'basicConstraints=CA:FALSE' \
  'keyUsage=digitalSignature,keyEncipherment' \
  'extendedKeyUsage=serverAuth' \
  'subjectAltName=DNS:rabbitmq-tls,IP:127.0.0.1' >"$TLS_RUNTIME/server.ext"
openssl x509 -req -sha256 -days 2 \
  -in "$TLS_RUNTIME/server.csr" \
  -CA "$TLS_RUNTIME/ca-cert.pem" \
  -CAkey "$TLS_RUNTIME/ca-key.pem" \
  -CAcreateserial \
  -extfile "$TLS_RUNTIME/server.ext" \
  -out "$TLS_RUNTIME/server-cert.pem" >/dev/null 2>&1

openssl req -new -newkey rsa:2048 -sha256 -nodes \
  -keyout "$TLS_RUNTIME/client-key.pem" \
  -out "$TLS_RUNTIME/client.csr" \
  -subj '/CN=atlas-mtls-client' >/dev/null 2>&1
printf '%s\n' \
  'basicConstraints=CA:FALSE' \
  'keyUsage=digitalSignature,keyEncipherment' \
  'extendedKeyUsage=clientAuth' >"$TLS_RUNTIME/client.ext"
openssl x509 -req -sha256 -days 2 \
  -in "$TLS_RUNTIME/client.csr" \
  -CA "$TLS_RUNTIME/ca-cert.pem" \
  -CAkey "$TLS_RUNTIME/ca-key.pem" \
  -CAcreateserial \
  -extfile "$TLS_RUNTIME/client.ext" \
  -out "$TLS_RUNTIME/client-cert.pem" >/dev/null 2>&1

openssl req -new -newkey rsa:2048 -sha256 -nodes \
  -keyout "$TLS_RUNTIME/untrusted-ca-key.pem" \
  -out "$TLS_RUNTIME/untrusted-ca.csr" \
  -subj '/CN=RabbitMQ Reference Atlas Untrusted CA' >/dev/null 2>&1
openssl x509 -req -sha256 -days 2 \
  -in "$TLS_RUNTIME/untrusted-ca.csr" \
  -signkey "$TLS_RUNTIME/untrusted-ca-key.pem" \
  -extfile "$TLS_RUNTIME/ca.ext" \
  -out "$TLS_RUNTIME/untrusted-ca-cert.pem" >/dev/null 2>&1

chmod 644 "$TLS_RUNTIME"/*.pem

openssl verify -CAfile "$TLS_RUNTIME/ca-cert.pem" \
  "$TLS_RUNTIME/server-cert.pem" "$TLS_RUNTIME/client-cert.pem" >/dev/null

if [[ "${TLS_LAB_CERTIFICATE_CHECK_ONLY:-0}" == "1" ]]; then
  echo "Ephemeral TLS証明書Chainの検証が完了しました。"
  exit 0
fi

"${COMPOSE[@]}" up -d --wait

(cd "$ROOT" && go run ./cmd/rmq-tls-lab \
  --ca "$TLS_RUNTIME/ca-cert.pem" \
  --bad-ca "$TLS_RUNTIME/untrusted-ca-cert.pem" \
  --client-cert "$TLS_RUNTIME/client-cert.pem" \
  --client-key "$TLS_RUNTIME/client-key.pem" \
  --server-cert "$TLS_RUNTIME/server-cert.pem" \
  --output "$OUTPUT")

echo "RabbitMQ TLS/mTLS LabとRaw Evidence生成が完了しました。"
