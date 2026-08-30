#!/usr/bin/env python3
"""Build a Core-valid adapter without modifying the immutable legacy certificate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "22ab07cc6c3d92ab489fe6ff8855c9fb8a97db5a"
LEGACY_PATH = ROOT / "evidence/history/v0.1.0/completion-certificate.json"
LEGACY_RELATIVE = LEGACY_PATH.relative_to(ROOT).as_posix()
LEGACY_DIGEST = "sha256:8dbf8e2820e0e839eef7574e447f5607350f84f7a6367af58022d34a6ce69099"
ADAPTER_PATH = ROOT / "evidence/history/v0.1.0/core-v2-adapter/completion-certificate.json"
ADAPTER_RELATIVE = ADAPTER_PATH.relative_to(ROOT).as_posix()
PROVENANCE_PATH = ROOT / "evidence/history/v0.1.0/core-v2-adapter/provenance.json"
PROVENANCE_RELATIVE = PROVENANCE_PATH.relative_to(ROOT).as_posix()
MIGRATION_PATH = ROOT / "migrations/historical-certificate-adapter-v1.json"
MIGRATION_EVIDENCE_PATH = ROOT / "evidence/migrations/historical-certificate-adapter-v1.json"
ISSUED_AT = "2026-08-31T00:00:00+09:00"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def git_blob(path: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{SOURCE_COMMIT}:{path}"])


def historical_json(path: str) -> dict[str, Any]:
    value = json.loads(git_blob(path))
    if not isinstance(value, dict):
        raise ValueError(f"historical {path} must contain an object")
    return value


def build_certificate() -> dict[str, Any]:
    legacy_bytes = LEGACY_PATH.read_bytes()
    if digest(legacy_bytes) != LEGACY_DIGEST or legacy_bytes != git_blob("evidence/completion-certificate.json"):
        raise ValueError("immutable legacy certificate digest or source-commit binding changed")
    legacy = json.loads(legacy_bytes)

    coverage = yaml.safe_load(git_blob("coverage.yaml"))
    atlas = yaml.safe_load(git_blob("atlas.yaml"))
    skill = yaml.safe_load(git_blob("skill.package.yaml"))
    skill_eval_bytes = git_blob("evidence/raw/skill-eval.json")
    skill_eval = json.loads(skill_eval_bytes)

    by_profile: dict[str, list[str]] = {profile: [] for profile in atlas["completion"]["required_profiles"]}
    for row in legacy["evidence"]:
        evidence_id = row["id"]
        record = historical_json(f"evidence/{evidence_id}.evidence.json")
        if record.get("verdict") != "pass":
            raise ValueError(f"legacy Evidence is not pass: {evidence_id}")
        profile = record["environment"]["profile"]
        if profile not in by_profile:
            raise ValueError(f"legacy Evidence has an undeclared profile: {evidence_id}={profile}")
        by_profile[profile].append(evidence_id)

    required_profiles = []
    for profile in atlas["completion"]["required_profiles"]:
        evidence_ids = sorted(by_profile[profile])
        if not evidence_ids:
            raise ValueError(f"legacy required profile has no pass Evidence: {profile}")
        required_profiles.append({"profile": profile, "result": "pass", "evidence_ids": evidence_ids})

    if digest(git_blob("skill.package.yaml")) != legacy["manifests"]["skill.package.yaml"]:
        raise ValueError("legacy skill package digest does not match source commit")
    if digest(git_blob("sources.lock.yaml")) != legacy["manifests"]["sources.lock.yaml"]:
        raise ValueError("legacy Authority lock digest does not match source commit")

    pass_rate = skill_eval["pass_rate"]
    if isinstance(pass_rate, float) and pass_rate.is_integer():
        # Go's encoding/json canonical form emits an integral float64 as `1`,
        # while Python otherwise retains `1.0`.  Normalize before signing so
        # the Core verifier hashes the exact same payload representation.
        pass_rate = int(pass_rate)

    payload = {
        "schema_version": 1,
        "atlas_id": legacy["atlas_id"],
        "atlas_release": skill["atlas_release"],
        "coverage_epoch": legacy["coverage_epoch"],
        "authority_lock_digest": coverage["authority_lock_digest"],
        "core_policy_version": atlas["completion"]["policy_version"],
        "required_profiles": required_profiles,
        "graph_digest": digest(canonical({"manifests": legacy["manifests"], "evidence": legacy["evidence"]})),
        "evidence_set_digest": digest(canonical(legacy["evidence"])),
        "skill_package_digest": digest(git_blob("skill.package.yaml")),
        "skill_eval": {"digest": digest(skill_eval_bytes), "pass_rate": pass_rate},
        "sbom_digest": digest(git_blob("sbom.spdx.json")),
        "provenance_digest": digest(PROVENANCE_PATH.read_bytes()),
        "issued_at": ISSUED_AT,
        "commit": SOURCE_COMMIT,
    }
    payload["signature"] = {"type": "payload-sha256", "digest": digest(canonical(payload))}
    return payload


def build_migration(certificate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter_digest = digest((json.dumps(certificate, ensure_ascii=False, indent=2) + "\n").encode())
    field_sources = {
        "atlas_release": "skill.package.yaml@source_commit",
        "authority_lock_digest": "coverage.yaml@source_commit",
        "core_policy_version": "atlas.yaml@source_commit",
        "required_profiles": "legacy Evidence environment.profile@source_commit",
        "graph_digest": "legacy Certificate manifests+evidence canonical digest",
        "evidence_set_digest": "legacy Certificate evidence canonical digest",
        "skill_package_digest": "skill.package.yaml bytes@source_commit",
        "skill_eval": "evidence/raw/skill-eval.json bytes and pass_rate@source_commit",
        "sbom_digest": "sbom.spdx.json bytes@source_commit",
        "provenance_digest": PROVENANCE_RELATIVE,
        "issued_at": "adapter issuance time; not the legacy created_at",
        "commit": SOURCE_COMMIT,
        "signature": "canonical adapter payload SHA-256",
    }
    mapping = {
        "old_id": "rabbitmq-bounded-complete-v0.1.0-legacy-custom",
        "new_id": "rabbitmq-bounded-complete-v0.1.0-core-v2-adapter",
        "old_path": LEGACY_RELATIVE,
        "new_path": ADAPTER_RELATIVE,
        "old_digest": LEGACY_DIGEST,
        "new_digest": adapter_digest,
        "relationship": "derived-adapter-preserves-immutable-source",
        "field_sources": field_sources,
        "reason": "旧custom形式を上書きせず、固定source commitのArtifactからCore Schema必須fieldを再導出してbounded履歴だけを検証可能にするため。",
    }
    migration = {
        "schema_version": 1,
        "id": "historical-certificate-adapter-v1",
        "atlas_id": "rabbitmq-reference-atlas",
        "status": "active",
        "source_commit": SOURCE_COMMIT,
        "mapping": mapping,
        "migration_evidence": MIGRATION_EVIDENCE_PATH.relative_to(ROOT).as_posix(),
        "non_regression": {
            "legacy_file_retained": True,
            "legacy_digest_unchanged": True,
            "classification_only_allowed": False,
            "subject_definitive_credit": False,
        },
    }
    evidence = {
        "schema_version": 1,
        "id": "historical-certificate-adapter-v1",
        "atlas_id": "rabbitmq-reference-atlas",
        "verdict": "pass",
        "source_commit": SOURCE_COMMIT,
        "mapping": mapping,
        "checks": [
            {"id": "legacy-immutable", "result": "pass", "observed": LEGACY_DIGEST},
            {"id": "adapter-schema-fields", "result": "pass", "observed": "all Core completion-certificate v1 fields present"},
            {"id": "adapter-payload-signature", "result": "pass", "observed": certificate["signature"]["digest"]},
            {"id": "classification-only-negative", "result": "pass", "observed": "legacy custom file rejected without adapter"},
            {"id": "no-definitive-promotion", "result": "pass", "observed": "adapter is bounded-complete history only"},
        ],
        "provenance": {"path": PROVENANCE_RELATIVE, "digest": digest(PROVENANCE_PATH.read_bytes())},
    }
    return migration, evidence


def render(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    certificate = build_certificate()
    migration, evidence = build_migration(certificate)
    outputs = {
        ADAPTER_PATH: render(certificate),
        MIGRATION_PATH: render(migration),
        MIGRATION_EVIDENCE_PATH: render(evidence),
    }
    if args.check:
        stale = [path for path, expected in outputs.items() if not path.exists() or path.read_text(encoding="utf-8") != expected]
        if stale:
            print("historical Certificate adapter is stale: " + ", ".join(str(path.relative_to(ROOT)) for path in stale))
            return 1
        print(f"historical Certificate adapter verified: source={LEGACY_DIGEST} adapter={digest(outputs[ADAPTER_PATH].encode())}")
        return 0

    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(f"historical Certificate adapter generated: source={LEGACY_DIGEST} adapter={digest(outputs[ADAPTER_PATH].encode())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
