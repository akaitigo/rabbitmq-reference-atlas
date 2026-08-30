#!/usr/bin/env python3
"""security-002が実Runtime前提を縮小しないことを固定する。"""

from __future__ import annotations

import copy
import base64
import importlib.util
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "runtime/security-002.contract.yaml"
FIXTURE_DIR = ROOT / "fixtures/security-002"
EXPECTED_ROWS = {
    "closure.ldap.tls.security": ("ldap.tls", ["node-1-with-ldap", "node-2-with-ldap", "node-3-with-ldap"]),
    "closure.limit.queues.security": ("limit.queues", ["node-1-workload", "node-2-workload", "node-3-workload"]),
    "closure.management.authorization.security": ("management.authorization", ["node-1", "node-2", "node-3"]),
    "closure.management.oauth.security": ("management.oauth", ["node-1", "node-2", "node-3"]),
}

REPORTER_SPEC = importlib.util.spec_from_file_location(
    "generate_security_002_runtime", ROOT / "scripts/generate-security-002-runtime.py"
)
if REPORTER_SPEC is None or REPORTER_SPEC.loader is None:
    raise RuntimeError("security-002 runtime reporterをloadできません")
reporter = importlib.util.module_from_spec(REPORTER_SPEC)
REPORTER_SPEC.loader.exec_module(reporter)


class ContractError(ValueError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"document-not-object:{path}")
    return value


def validate(contract: dict[str, Any]) -> None:
    if contract.get("id") != "security-002" or contract.get("status") != "planned-runtime":
        raise ContractError("status-must-remain-planned-runtime")
    if contract.get("completion_credit") is not False:
        raise ContractError("pre-runtime-completion-credit-forbidden")
    rows = contract.get("rows", [])
    if len(rows) != 4 or [row.get("closure_id") for row in rows] != list(EXPECTED_ROWS):
        raise ContractError("closure-row-set-or-order-changed")

    closure_plan = json.loads((ROOT / contract["closure_plan"]).read_text(encoding="utf-8"))
    closure_rows = {row["id"]: row for row in closure_plan["rows"]}
    forbidden = set(contract["proof_policy"]["forbidden_substitutions"])
    for row in rows:
        closure_id = row["closure_id"]
        behavior, variants = EXPECTED_ROWS[closure_id]
        if row.get("behavior_id") != behavior or row.get("variants") != variants:
            raise ContractError("closure-row-binding-changed")
        if closure_rows[closure_id]["variant_ids"] != variants:
            raise ContractError("closure-plan-variant-mismatch")
        if set(row.get("required_artifact_channels", [])) != {"packet", "log", "metric"}:
            raise ContractError("artifact-channel-missing")
        if len(row.get("required_oracles", [])) < (1 if row["state"] == "preserve-existing-runtime" else 3):
            raise ContractError("required-oracle-missing")
        flattened = yaml.safe_dump(row, allow_unicode=True)
        if any(item in flattened for item in forbidden):
            raise ContractError("forbidden-substitution")
        if row["state"] == "preserve-existing-runtime":
            report = ROOT / row["existing_runtime_report"]
            if not report.is_file():
                raise ContractError("existing-runtime-report-missing")
        elif row["state"] != "pending-runtime":
            raise ContractError("runtime-state-weakened")

    policy = contract["proof_policy"]
    required = {
        "attempts": 1,
        "retries": 0,
        "first_attempt_required": True,
        "dedicated_report_per_row": True,
        "source_digest_required": True,
        "harness_digest_required": True,
        "runtime_identity_required": True,
        "atomic_publication_required": True,
    }
    if any(policy.get(key) != value for key, value in required.items()):
        raise ContractError("proof-policy-weakened")
    resources = contract["resource_policy"]
    if resources.get("minimum_free_bytes_before_runtime", 0) < 4294967296:
        raise ContractError("disk-preflight-weakened")
    if not all(resources.get(key) is True for key in (
        "sequential_environments", "tmpfs_broker_state", "remove_orphans_on_exit",
        "remove_task_owned_volumes_on_exit", "global_prune_forbidden", "foreign_resource_mutation_forbidden",
    )):
        raise ContractError("resource-isolation-weakened")


def mutate(document: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(document)
    parts = fixture["mutation_path"].split(".")
    cursor: Any = value
    for part in parts[:-1]:
        cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
    leaf = int(parts[-1]) if isinstance(cursor, list) else parts[-1]
    if fixture.get("operation") == "delete":
        del cursor[leaf]
    else:
        cursor[leaf] = fixture["replacement"]
    return value


contract = load_yaml(CONTRACT_PATH)
validate(contract)
fixtures = sorted(FIXTURE_DIR.glob("*.yaml"))
if len(fixtures) != 4:
    raise ContractError("negative-fixture-count-changed")
for path in fixtures:
    fixture = load_yaml(path)
    try:
        validate(mutate(contract, fixture))
    except ContractError as error:
        if str(error) != fixture["expected_error"]:
            raise ContractError(f"fixture-error-mismatch:{path.name}:{error}") from error
    else:
        raise ContractError(f"negative-fixture-accepted:{path.name}")

assert all(not Path(item).is_absolute() for item in (*reporter.LDAP_COMPOSE, *reporter.OAUTH_COMPOSE))
assert [node["proof_variant"] for node in reporter.LDAP_NODES] == [
    "node-1-with-ldap", "node-2-with-ldap", "node-3-with-ldap",
]
assert [node["proof_variant"] for node in reporter.LIMIT_NODES] == [
    "node-1-workload", "node-2-workload", "node-3-workload",
]
assert [node["variant"] for node in reporter.OAUTH_NODES] == ["node-1", "node-2", "node-3"]

payload = base64.urlsafe_b64encode(json.dumps({
    "iss": "http://keycloak:8080/realms/rabbitmq",
    "aud": "rabbitmq",
    "scope": "rabbitmq.tag:management",
    "preferred_username": "fixture-user",
}).encode()).decode().rstrip("=")
fixture_token = f"header.{payload}.fixture-signature-secret"
returned_token, summary = reporter.summarize_oauth_token("fixture-client", {
    "access_token": fixture_token, "token_type": "Bearer", "expires_in": 60,
})
assert returned_token == fixture_token
assert summary["token_value"] == "[redacted]"
assert "fixture-signature-secret" not in json.dumps(summary)

print("security-002 contract PASS: 4 rows/12 variants、実IdP・不正Trust・queue limit・Artifact条件を固定")
