#!/usr/bin/env python3
"""AMQP 1.0 negotiationの四Scenarioを専用Evidenceへ固定する。"""

import hashlib
import json
import os
import pathlib

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = pathlib.Path(os.environ.get("RABBITMQ_EVIDENCE_ROOT", ROOT / "evidence"))
BEHAVIOR = "amqp10.version-negotiation"
TARGET_ID = f"definitive.{BEHAVIOR}"
CLAIM_ID = f"{TARGET_ID}.claim"
SCENARIOS = ("normal", "boundary", "rejection", "security")


def sha(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    source_digest = sha(ROOT / "sources.lock.yaml")
    harness_path = ROOT / "cmd/rmq-amqp10-handshake/main.go"
    environment_path = ROOT / "environments/compose.yaml"
    evidence_ids = []
    for scenario in SCENARIOS:
        artifact_path = EVIDENCE_ROOT / f"raw/definitive.{BEHAVIOR}.{scenario}.json"
        raw = json.loads(artifact_path.read_text())
        checks = raw.get("checks", [])
        if raw.get("behavior_id") != BEHAVIOR or raw.get("scenario") != scenario:
            raise SystemExit(f"artifact identity mismatch: {artifact_path}")
        if len(checks) != 3 or not raw.get("passed") or not all(item.get("passed") for item in checks):
            raise SystemExit(f"three-node oracle failed: {artifact_path}")
        evidence_id = f"definitive.{BEHAVIOR}.{scenario}"
        evidence_ids.append(evidence_id)
        record = {
            "schema_version": 1,
            "id": evidence_id,
            "atlas_id": "rabbitmq-reference-atlas",
            "claim_ids": [CLAIM_ID],
            "kind": "conformance",
            "producer": "cmd/rmq-amqp10-handshake",
            "command": "go run ./cmd/rmq-amqp10-handshake --endpoints 127.0.0.1:25672,127.0.0.1:25673,127.0.0.1:25674 --output-dir evidence/raw",
            "created_at": raw["created_at"],
            "environment": {
                "profile": "cluster",
                "runtime_profile": "protocol-amqp10",
                "manifest_digest": sha(environment_path),
                "nodes": 3,
                "protocol": "AMQP 1.0",
                "scenario": scenario,
            },
            "source_digest": source_digest,
            "harness_digest": sha(harness_path),
            "harness_path": harness_path.relative_to(ROOT).as_posix(),
            "execution_mode": "runtime",
            "runtime_identity": "rabbitmq:4.3.5-management@sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031; nodes=3",
            "artifact": {
                "uri": f"evidence/raw/definitive.{BEHAVIOR}.{scenario}.json",
                "digest": sha(artifact_path),
                "media_type": "application/json",
                "size_bytes": artifact_path.stat().st_size,
            },
            "verdict": "pass",
            "retention": "git",
        }
        record_path = EVIDENCE_ROOT / f"{evidence_id}.evidence.json"
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")

    if os.environ.get("RABBITMQ_EVIDENCE_ONLY") == "1":
        print(f"AMQP 1.0 negotiation evidence staged: scenarios={len(evidence_ids)} target={TARGET_ID}")
        return

    coverage_path = ROOT / "coverage.yaml"
    coverage = yaml.safe_load(coverage_path.read_text())
    target = next(item for item in coverage["targets"] if item["id"] == TARGET_ID)
    target["state"] = "covered"
    target["evidence_ids"] = evidence_ids
    coverage_path.write_text(yaml.safe_dump(coverage, allow_unicode=True, sort_keys=False))

    claim_path = ROOT / f"claims/{TARGET_ID}.claim.yaml"
    claim = yaml.safe_load(claim_path.read_text())
    claim["status"] = "accepted"
    claim_path.write_text(yaml.safe_dump(claim, allow_unicode=True, sort_keys=False))

    plan_path = ROOT / "verification.plan.yaml"
    plan = yaml.safe_load(plan_path.read_text())
    evidence_by_scenario = dict(zip(SCENARIOS, evidence_ids))
    for row in plan["rows"]:
        if row["behavior_id"] == BEHAVIOR and row["scenario"] in evidence_by_scenario:
            row["state"] = "covered"
            row["evidence_ids"] = [evidence_by_scenario[row["scenario"]]]
    plan_path.write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False))
    print(f"AMQP 1.0 negotiation evidence generated: scenarios={len(evidence_ids)} target={TARGET_ID}")


if __name__ == "__main__":
    main()
