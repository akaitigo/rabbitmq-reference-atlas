#!/usr/bin/env python3
"""MQTT/STOMP plugin protocol artifactsをDefinitive Graphへ接続する。"""

import hashlib
import json
import os
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path(os.environ.get("RABBITMQ_EVIDENCE_ROOT", ROOT / "evidence"))
BEHAVIORS = ("mqtt.protocol-versions-qos", "stomp.protocol-plugin")
SCENARIOS = ("normal", "boundary", "rejection", "compatibility")


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def framed(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def main() -> None:
    source_digest = sha(ROOT / "sources.lock.yaml")
    harness_digest = framed([ROOT / "cmd/rmq-plugin-protocols/main.go", ROOT / "scripts/run-plugin-protocol-lab.sh"])
    environment_digest = framed([ROOT / "environments/compose.yaml", ROOT / "labs/plugin-protocols/lab.yaml"])
    evidence_by_behavior = {}
    for behavior in BEHAVIORS:
        evidence_ids = []
        for scenario in SCENARIOS:
            artifact_path = EVIDENCE_ROOT / f"raw/definitive.{behavior}.{scenario}.json"
            raw = json.loads(artifact_path.read_text(encoding="utf-8"))
            if raw.get("behavior_id") != behavior or raw.get("scenario") != scenario:
                raise SystemExit(f"artifact identity mismatch: {artifact_path}")
            checks = raw.get("checks", [])
            if len(checks) != 3 or raw.get("passed") is not True or not all(item.get("passed") is True for item in checks):
                raise SystemExit(f"three-node oracle failed: {artifact_path}")
            evidence_id = f"definitive.{behavior}.{scenario}"
            evidence_ids.append(evidence_id)
            record = {
                "schema_version": 1,
                "id": evidence_id,
                "atlas_id": "rabbitmq-reference-atlas",
                "claim_ids": [f"definitive.{behavior}.claim"],
                "kind": "conformance",
                "producer": "cmd/rmq-plugin-protocols",
                "command": "bash scripts/run-plugin-protocol-lab.sh",
                "created_at": raw["created_at"],
                "environment": {
                    "profile": "cluster",
                    "runtime_profile": "plugin-mqtt" if behavior == "mqtt.protocol-versions-qos" else "plugin-stomp",
                    "manifest_digest": environment_digest,
                    "nodes": 3,
                    "rabbitmq_version": "4.3.5",
                    "official_plugins": ["rabbitmq_mqtt", "rabbitmq_stomp", "rabbitmq_stream"],
                    "scenario": scenario,
                },
                "source_digest": source_digest,
                "harness_digest": harness_digest,
                "harness_path": "cmd/rmq-plugin-protocols/main.go",
                "execution_mode": "runtime",
                "runtime_identity": "rabbitmq:4.3.5-management@sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031; nodes=3; official plugins",
                "artifact": {
                    "uri": f"evidence/raw/definitive.{behavior}.{scenario}.json",
                    "digest": sha(artifact_path),
                    "media_type": "application/json",
                    "size_bytes": artifact_path.stat().st_size,
                },
                "verdict": "pass",
                "retention": "git",
            }
            (EVIDENCE_ROOT / f"{evidence_id}.evidence.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence_by_behavior[behavior] = evidence_ids

    if os.environ.get("RABBITMQ_EVIDENCE_ONLY") == "1":
        print(f"plugin protocol evidence staged: behaviors={len(BEHAVIORS)} scenarios={len(BEHAVIORS) * len(SCENARIOS)}")
        return

    coverage_path = ROOT / "coverage.yaml"
    coverage = yaml.safe_load(coverage_path.read_text(encoding="utf-8"))
    for target in coverage["targets"]:
        behavior = target["id"].removeprefix("definitive.")
        if behavior in evidence_by_behavior:
            target["state"] = "covered"
            target["evidence_ids"] = evidence_by_behavior[behavior]
    coverage_path.write_text(yaml.safe_dump(coverage, allow_unicode=True, sort_keys=False), encoding="utf-8")

    for behavior in BEHAVIORS:
        claim_path = ROOT / f"claims/definitive.{behavior}.claim.yaml"
        claim = yaml.safe_load(claim_path.read_text(encoding="utf-8"))
        claim["status"] = "accepted"
        claim_path.write_text(yaml.safe_dump(claim, allow_unicode=True, sort_keys=False), encoding="utf-8")

    plan_path = ROOT / "verification.plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    for row in plan["rows"]:
        if row["behavior_id"] in evidence_by_behavior and row["scenario"] in SCENARIOS:
            row["state"] = "covered"
            row["evidence_ids"] = [f"definitive.{row['behavior_id']}.{row['scenario']}"]
    plan_path.write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"plugin protocol evidence generated: behaviors={len(BEHAVIORS)} scenarios={len(BEHAVIORS) * len(SCENARIOS)}")


if __name__ == "__main__":
    main()
