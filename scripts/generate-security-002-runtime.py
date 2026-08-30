#!/usr/bin/env python3
"""security-002専用のplanned runtime reporter。実行前はClosure creditを与えない。"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rabbitmq_scenario_runtime_base", ROOT / "scripts/generate-scenario-runtime.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("scenario runtime base moduleをloadできません")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

LDAP_COMPOSE = ["docker", "compose", "-f", "environments/security-002-ldap.compose.yaml"]
OAUTH_COMPOSE = ["docker", "compose", "-f", "environments/security-002-oauth.compose.yaml"]
KEYCLOAK_RUNTIME_IDENTITY = {
    "product": "Keycloak",
    "version": "26.7.2",
    "image_digest": "sha256:9d1f1b2b7261ff53c66cb1092dfcdc34a5fb77e81f9e6a6e75b8b6a795de8067",
    "runtime_kind": "actual-openid-provider",
}
REPORTER_EXTENSION_IDENTITY = {
    "runtime_kind": "actual-harness-extension",
    "path": "scripts/generate-security-002-runtime.py",
    "source_digest": base.sha(ROOT / "scripts/generate-security-002-runtime.py"),
}
LDAP_NODES = (
    {"variant": "node-1", "proof_variant": "node-1-with-ldap", "service": "security-rabbitmq-1", "amqp": ("127.0.0.1", 27672)},
    {"variant": "node-2", "proof_variant": "node-2-with-ldap", "service": "security-rabbitmq-2", "amqp": ("127.0.0.1", 27673)},
    {"variant": "node-3", "proof_variant": "node-3-with-ldap", "service": "security-rabbitmq-3", "amqp": ("127.0.0.1", 27674)},
)
LIMIT_NODES = tuple({**node, "proof_variant": node["variant"] + "-workload"} for node in LDAP_NODES)
OAUTH_NODES = (
    {"variant": "node-1", "service": "oauth-rabbitmq-1", "oauth_management": "http://127.0.0.1:37672"},
    {"variant": "node-2", "service": "oauth-rabbitmq-2", "oauth_management": "http://127.0.0.1:37673"},
    {"variant": "node-3", "service": "oauth-rabbitmq-3", "oauth_management": "http://127.0.0.1:37674"},
)


def bearer_get(endpoint: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(endpoint + "/api/overview")
    request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read()
            return {"status": response.status, "body": json.loads(raw) if raw else None, "error": None}
    except urllib.error.HTTPError as error:
        return {"status": error.code, "body": error.read().decode("utf-8", errors="replace"), "error": str(error)}
    except Exception as error:
        return {"status": None, "body": None, "error": str(error)}


def summarize_oauth_token(client_id: str, result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    token = result.get("access_token")
    if not isinstance(token, str) or token.count(".") != 2:
        raise RuntimeError(f"Keycloak did not issue a JWT access token for {client_id}")
    payload_segment = token.split(".")[1]
    payload_segment += "=" * (-len(payload_segment) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_segment))
    return token, {
        "client_id": client_id,
        "token_type": result.get("token_type"),
        "expires_in": result.get("expires_in"),
        "issuer": payload.get("iss"),
        "audience": payload.get("aud"),
        "scope": payload.get("scope"),
        "preferred_username": payload.get("preferred_username"),
        "signed_jwt_segments": 3,
        "token_value": "[redacted]",
    }


def oauth_token(client_id: str) -> tuple[str, dict[str, Any]]:
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id": client_id,
        "username": "atlas-oauth-user",
        "password": "atlas-oauth-local-only",
        "scope": "openid profile",
    }).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:28080/realms/rabbitmq/protocol/openid-connect/token",
        data=data,
        method="POST",
    )
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read())
    return summarize_oauth_token(client_id, result)


def ldap_limits() -> None:
    raw = base.load_json(base.EVIDENCE_ROOT / "raw/security-002-ldap-limits.json")
    checks = raw.get("checks", [])
    if raw.get("passed") is not True or len(checks) != 3:
        raise RuntimeError(f"security-002 LDAP/limit client failed: {raw}")
    by_variant = {item["variant"]: item for item in checks}
    tls_packets, tls_assertions, limit_packets, limit_assertions = {}, {}, {}, {}
    for node in LDAP_NODES:
        check = by_variant.get(node["variant"])
        if not check or check.get("passed") is not True:
            raise RuntimeError(f"security-002 client variant failed: {node['variant']}: {check}")
        verified = base.run([
            *LDAP_COMPOSE, "exec", "-T", node["service"], "openssl", "s_client",
            "-connect", "ldap-directory:1636", "-CAfile", "/etc/rabbitmq/ldap-ca/ca.crt",
            "-verify_return_error", "-brief",
        ])
        untrusted = base.run([
            *LDAP_COMPOSE, "exec", "-T", node["service"], "openssl", "s_client",
            "-connect", "ldap-directory:1636", "-CAfile", "/etc/rabbitmq/ldap-ca/wrong-ca.crt",
            "-verify_return_error", "-brief",
        ])
        if verified["returncode"] != 0 or untrusted["returncode"] == 0:
            raise RuntimeError(f"LDAP TLS trust oracle failed: {node['service']}: verified={verified} untrusted={untrusted}")
        outcomes = {item["operation"]: item for item in check["outcomes"]}
        tls_packets[node["variant"]] = {
            "transport": "LDAPS plus AMQP 0-9-1",
            "endpoint": check["endpoint"],
            "correct_credential": outcomes["ldap-tls-correct-credential"],
            "wrong_credential": outcomes["ldap-tls-wrong-credential"],
            "trusted_ca_probe": verified,
            "untrusted_ca_probe": untrusted,
        }
        tls_assertions[node["variant"]] = [
            "RabbitMQ LDAP backendがCA検証付きLDAPS経由で正しいCredentialを許可した",
            "誤Credentialを拒否した",
            "同じbroker nodeから不正CAによるTLS接続を拒否した",
        ]
        limit_packets[node["variant"]] = {
            "transport": "AMQP 0-9-1 plus Management HTTP",
            "endpoint": check["endpoint"],
            "vhost": check["vhost"],
            "below_limit": outcomes["queue-below-limit"],
            "over_limit": outcomes["queue-over-limit"],
            "unprivileged_limit_mutation": outcomes["unprivileged-limit-mutation"],
        }
        limit_assertions[node["variant"]] = [
            "専用vhostで1個目のqueue宣言を許可した",
            "2個目のqueue宣言をmax-queuesで拒否した",
            "management tag無しLDAP userによるlimit変更を拒否した",
        ]
    dependencies = [base.LDAP_RUNTIME_IDENTITY, REPORTER_EXTENSION_IDENTITY]
    base.report(
        "ldap.tls", "security", "plugin-ldap-directory", "cmd/rmq-security-002/main.go",
        "rmq-security-002", tls_packets, tls_assertions, nodes=LDAP_NODES,
        compose=LDAP_COMPOSE, runtime_dependencies=dependencies,
    )
    base.report(
        "limit.queues", "security", "capacity-benchmark", "cmd/rmq-security-002/main.go",
        "rmq-security-002", limit_packets, limit_assertions, nodes=LIMIT_NODES,
        compose=LDAP_COMPOSE, runtime_dependencies=dependencies,
    )


def oauth() -> None:
    discovery_url = "http://127.0.0.1:28080/realms/rabbitmq/.well-known/openid-configuration"
    with urllib.request.urlopen(discovery_url, timeout=10) as response:
        discovery = json.loads(response.read())
    if discovery.get("issuer") != "http://keycloak:8080/realms/rabbitmq" or not discovery.get("jwks_uri"):
        raise RuntimeError(f"Keycloak discovery oracle failed: {discovery}")
    packets, assertions = {}, {}
    for node in OAUTH_NODES:
        allowed_token, allowed_summary = oauth_token("rabbitmq-management")
        no_scope_token, no_scope_summary = oauth_token("rabbitmq-no-management")
        wrong_audience_token, wrong_audience_summary = oauth_token("rabbitmq-wrong-audience")
        allowed = bearer_get(node["oauth_management"], allowed_token)
        no_scope = bearer_get(node["oauth_management"], no_scope_token)
        wrong_audience = bearer_get(node["oauth_management"], wrong_audience_token)
        tampered_token = allowed_token[:-1] + ("A" if allowed_token[-1] != "A" else "B")
        tampered = bearer_get(node["oauth_management"], tampered_token)
        if allowed["status"] != 200 or no_scope["status"] not in (401, 403) or wrong_audience["status"] not in (401, 403) or tampered["status"] not in (401, 403):
            raise RuntimeError(
                f"OAuth management oracle failed: {node['service']}: "
                f"allowed={allowed['status']} no_scope={no_scope['status']} "
                f"wrong_aud={wrong_audience['status']} tampered={tampered['status']}"
            )
        packets[node["variant"]] = {
            "transport": "OIDC discovery, OAuth 2.0 token endpoint and Management HTTP Bearer",
            "management_endpoint": node["oauth_management"] + "/api/overview",
            "discovery": {
                "issuer": discovery["issuer"],
                "jwks_uri": discovery["jwks_uri"],
                "token_endpoint": discovery["token_endpoint"],
            },
            "allowed_token": allowed_summary,
            "allowed_response": {"status": allowed["status"]},
            "no_management_scope_token": no_scope_summary,
            "no_management_scope_response": {"status": no_scope["status"]},
            "wrong_audience_token": wrong_audience_summary,
            "wrong_audience_response": {"status": wrong_audience["status"]},
            "tampered_token_response": {"status": tampered["status"]},
        }
        assertions[node["variant"]] = [
            "Keycloak discoveryとJWKSに結ばれた正しいaudience/management scopeの署名tokenを許可した",
            "management scope無しtokenと誤audience tokenを拒否した",
            "改変tokenを拒否した",
        ]
    base.report(
        "management.oauth", "security", "broker-cluster-3", "scripts/generate-security-002-runtime.py",
        "Keycloak-26.7.2-password-grant-client", packets, assertions, nodes=OAUTH_NODES,
        compose=OAUTH_COMPOSE, runtime_dependencies=[KEYCLOAK_RUNTIME_IDENTITY, REPORTER_EXTENSION_IDENTITY],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ldap-limits", "oauth"))
    args = parser.parse_args()
    if not os.environ.get("RABBITMQ_EVIDENCE_RUN_TOKEN") or not os.environ.get("RABBITMQ_EVIDENCE_RERUN_AT"):
        raise SystemExit("RABBITMQ_EVIDENCE_RUN_TOKEN and RABBITMQ_EVIDENCE_RERUN_AT are required")
    if args.command == "ldap-limits":
        ldap_limits()
    else:
        oauth()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
