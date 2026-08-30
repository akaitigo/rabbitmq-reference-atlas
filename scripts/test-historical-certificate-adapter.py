#!/usr/bin/env python3
"""Reject classification-only or unmapped historical Certificate promotion."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/generate-historical-certificate-adapter.py"
SPEC = importlib.util.spec_from_file_location("generate_historical_certificate_adapter", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


REQUIRED = {
    "schema_version", "atlas_id", "atlas_release", "coverage_epoch", "authority_lock_digest",
    "core_policy_version", "required_profiles", "graph_digest", "evidence_set_digest",
    "skill_package_digest", "skill_eval", "sbom_digest", "provenance_digest", "issued_at",
    "commit", "signature",
}


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def validate_bundle(definitive: dict[str, Any], legacy_bytes: bytes, certificate: dict[str, Any], migration: dict[str, Any]) -> None:
    if sha(legacy_bytes) != MODULE.LEGACY_DIGEST:
        raise ValueError("legacy Certificate changed")
    if set(certificate) != REQUIRED:
        raise ValueError("adapter Certificate schema fields are incomplete")
    signature = certificate["signature"]
    payload = {key: value for key, value in certificate.items() if key != "signature"}
    if signature != {"type": "payload-sha256", "digest": sha(MODULE.canonical(payload))}:
        raise ValueError("adapter payload signature mismatch")
    historical = definitive["historical_certificates"]
    if historical != [{"path": MODULE.ADAPTER_RELATIVE, "classification": "bounded-complete"}]:
        raise ValueError("definitive base must reference only the mapped adapter")
    mapping = migration.get("mapping", {})
    if mapping.get("old_path") != MODULE.LEGACY_RELATIVE or mapping.get("new_path") != MODULE.ADAPTER_RELATIVE:
        raise ValueError("old-to-new mapping is missing")
    if mapping.get("old_digest") != MODULE.LEGACY_DIGEST:
        raise ValueError("mapping legacy digest mismatch")
    if mapping.get("new_digest") != sha(MODULE.render(certificate).encode()):
        raise ValueError("mapping adapter digest mismatch")
    if migration.get("non_regression", {}).get("classification_only_allowed") is not False:
        raise ValueError("classification-only migration must be rejected")
    if migration.get("non_regression", {}).get("subject_definitive_credit") is not False:
        raise ValueError("bounded adapter cannot receive definitive credit")


def must_reject(label: str, function) -> None:
    try:
        function()
    except (ValueError, KeyError, TypeError):
        return
    raise AssertionError(f"negative fixture was accepted: {label}")


definitive = yaml.safe_load((ROOT / "definitive.yaml").read_text(encoding="utf-8"))
legacy_bytes = MODULE.LEGACY_PATH.read_bytes()
certificate = json.loads(MODULE.ADAPTER_PATH.read_text(encoding="utf-8"))
migration = json.loads(MODULE.MIGRATION_PATH.read_text(encoding="utf-8"))
evidence = json.loads(MODULE.MIGRATION_EVIDENCE_PATH.read_text(encoding="utf-8"))
validate_bundle(definitive, legacy_bytes, certificate, migration)
assert evidence["verdict"] == "pass" and evidence["mapping"] == migration["mapping"]

classification_only = copy.deepcopy(definitive)
classification_only["historical_certificates"] = [{"path": MODULE.LEGACY_RELATIVE, "classification": "bounded-complete"}]
must_reject("classification only", lambda: validate_bundle(classification_only, legacy_bytes, json.loads(legacy_bytes), migration))

bad_signature = copy.deepcopy(certificate)
bad_signature["signature"]["digest"] = "sha256:" + "0" * 64
must_reject("payload signature mutation", lambda: validate_bundle(definitive, legacy_bytes, bad_signature, migration))

missing_mapping = copy.deepcopy(migration)
missing_mapping.pop("mapping")
must_reject("mapping deletion", lambda: validate_bundle(definitive, legacy_bytes, certificate, missing_mapping))

mutated_legacy = legacy_bytes + b"\n"
must_reject("legacy overwrite", lambda: validate_bundle(definitive, mutated_legacy, certificate, migration))

print("historical Certificate adapter negative contract PASS: immutable legacy、old-to-new mapping、payload署名、classification-only拒否")
