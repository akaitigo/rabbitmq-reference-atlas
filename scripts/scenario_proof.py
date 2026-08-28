#!/usr/bin/env python3
"""RabbitMQ Reference SystemとBehavior固有Scenario Proofを分離して構築・検証する。"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROOF_ROOT = ROOT / "evidence/scenarios"
BEHAVIOR_ROOT = PROOF_ROOT / "behaviors"
INDEX_PATH = PROOF_ROOT / "index.json"
REFERENCE_RESULT_PATH = ROOT / "reference-system/results.json"
SCENARIOS = ["normal", "boundary", "rejection", "failure", "recovery", "migration", "operations", "security", "performance", "compatibility"]
GENERATED_AT = "2026-08-28T00:00:00+09:00"
CHANNELS = ("packet", "log", "metric")


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def safe_behavior_path(behavior_id: str) -> str:
    return behavior_id.replace("/", "_")


def authority_artifacts(inventory: dict) -> dict[str, dict]:
    out = {}
    for descriptor in inventory["authority_artifacts"]:
        document = load_yaml(ROOT / descriptor["path"])
        out[descriptor["id"]] = {"descriptor": descriptor, "document": document}
    return out


def decision_behavior_ids(decision: dict) -> set[str]:
    values: list[str] = []
    for key in ("behavior_ids", "promoted_behavior_ids"):
        if isinstance(decision.get(key), list):
            values.extend(str(item) for item in decision[key])
    mapping = decision.get("mapping", {})
    if isinstance(mapping, dict):
        for key in ("behavior_ids", "promoted_behavior_ids"):
            if isinstance(mapping.get(key), list):
                values.extend(str(item) for item in mapping[key])
    return set(values)


def human_decision_for(behavior_id: str, decisions: dict) -> dict | None:
    for decision in decisions.get("decisions", []):
        if behavior_id in decision_behavior_ids(decision):
            return decision
    return None


def reference_edge(item: dict, artifacts: dict[str, dict]) -> dict:
    artifact = artifacts[item["authority_artifact_id"]]
    descriptor, document = artifact["descriptor"], artifact["document"]
    surface = next(entry for entry in document["surfaces"] if entry["id"] == item["authority_surface_id"])
    return {
        "inventory_id": item["id"],
        "authority_artifact_id": descriptor["id"],
        "authority_artifact_path": descriptor["path"],
        "authority_artifact_digest": descriptor["digest"],
        "source_id": descriptor["source_id"],
        "source_digest": document["source_digest"],
        "surface_id": surface["id"],
        "locator": surface["locator"],
        "behavior_id": surface["behavior_id"],
    }


def channel_bindings(record: dict | None, raw: dict | None) -> tuple[dict[str, dict], list[str]]:
    channels = {name: {"artifacts": [], "gap_ids": []} for name in CHANNELS}
    if not record or not raw:
        gaps = []
        for name in CHANNELS:
            gap = f"artifact.{name}.missing"
            channels[name]["gap_ids"].append(gap)
            gaps.append(gap)
        return channels, gaps

    artifact = record["artifact"]
    log_binding = {
        "channel": "client-log",
        "path": artifact["uri"],
        "digest": artifact["digest"],
        "media_type": artifact["media_type"],
        "size_bytes": artifact["size_bytes"],
    }
    channels["log"]["artifacts"].append(log_binding)

    raw_text = json.dumps(raw, sort_keys=True).lower()
    if "sent_hex" in raw_text or "received_hex" in raw_text or "packet_hex" in raw_text:
        channels["packet"]["artifacts"].append({**log_binding, "channel": "wire-packet-record"})
    else:
        channels["packet"]["gap_ids"].append("artifact.packet.capture-missing")

    if any(key in raw_text for key in ('"metrics"', '"samples"', '"latency_ms"', '"rate_per_second"')):
        channels["metric"]["artifacts"].append({**log_binding, "channel": "metric-record"})
    else:
        channels["metric"]["gap_ids"].append("artifact.metric.record-missing")
    gaps = [gap for name in CHANNELS for gap in channels[name]["gap_ids"]]
    return channels, gaps


def identities(record: dict | None) -> tuple[dict[str, Any], list[str]]:
    if not record:
        return {
            "broker": None,
            "client": None,
            "runtime": None,
        }, ["identity.broker.missing", "identity.client-version.missing", "identity.runtime.missing"]
    environment = record.get("environment", {})
    runtime_identity = record.get("runtime_identity")
    broker = {
        "product": "RabbitMQ",
        "version": "4.3.5",
        "identity": runtime_identity,
        "manifest_digest": environment.get("manifest_digest"),
        "nodes": environment.get("nodes"),
    }
    client = {
        "name": record.get("producer"),
        "version_kind": "harness-sha256",
        "version": record.get("harness_digest"),
        "source": record.get("harness_path"),
    }
    runtime = {
        "profile": environment.get("runtime_profile"),
        "execution_mode": record.get("execution_mode"),
        "created_at": record.get("created_at"),
        "identity": runtime_identity,
    }
    gaps = []
    if not all((broker["version"], broker["identity"], broker["manifest_digest"], broker["nodes"])):
        gaps.append("identity.broker.incomplete")
    if not all((client["name"], client["version"], client["source"])):
        gaps.append("identity.client-version.incomplete")
    if not all((runtime["profile"], runtime["execution_mode"], runtime["created_at"], runtime["identity"])):
        gaps.append("identity.runtime.incomplete")
    return {"broker": broker, "client": client, "runtime": runtime}, gaps


def exact_behavior_evidence(row: dict, inventory_item: dict, records: dict[str, dict]) -> tuple[dict | None, dict | None, list[str]]:
    gaps = []
    evidence_ids = row.get("evidence_ids", [])
    if row["applicability"] != "required":
        return None, None, ["scenario.not-applicable-by-surface-contract"]
    if len(evidence_ids) != 1:
        return None, None, ["evidence.behavior-specific-single-record-missing"]
    record = records.get(evidence_ids[0])
    if not record:
        return None, None, ["evidence.record-missing"]
    if record.get("verdict") != "pass" or record.get("execution_mode") not in {"runtime", "platform"}:
        gaps.append("evidence.runtime-pass-missing")
    if record.get("claim_ids") != inventory_item["claim_ids"]:
        gaps.append("evidence.claim-binding-mismatch")
    artifact_path = ROOT / record.get("artifact", {}).get("uri", "")
    raw = None
    if not artifact_path.is_file():
        gaps.append("evidence.raw-artifact-missing")
    else:
        artifact = record["artifact"]
        if sha_file(artifact_path) != artifact.get("digest") or artifact_path.stat().st_size != artifact.get("size_bytes"):
            gaps.append("evidence.raw-artifact-digest-mismatch")
        raw = load_json(artifact_path)
        if raw.get("behavior_id") != row["behavior_id"] or raw.get("scenario") != row["scenario"]:
            gaps.append("evidence.aggregate-or-cross-row-binding")
        if raw.get("passed") is not True:
            gaps.append("evidence.raw-verdict-not-pass")
    return record, raw, gaps


def build_reference_results(manifest: dict) -> dict:
    rows = []
    for scenario in manifest["scenario_order"]:
        rows.append({
            "id": f"reference-system.{scenario}",
            "scenario": scenario,
            "status": "gap-unexecuted",
            "broker_identity": None,
            "client_identity": None,
            "runtime_identity": None,
            "artifact_channels": {
                "packet": {"artifacts": [], "gap_ids": [f"reference.{scenario}.packet-missing"]},
                "log": {"artifacts": [], "gap_ids": [f"reference.{scenario}.log-missing"]},
                "metric": {"artifacts": [], "gap_ids": [f"reference.{scenario}.metric-missing"]},
            },
            "completion_eligible": False,
            "gap_ids": [
                f"reference.{scenario}.execution-missing",
                f"reference.{scenario}.broker-client-runtime-identity-missing",
                f"reference.{scenario}.packet-missing",
                f"reference.{scenario}.log-missing",
                f"reference.{scenario}.metric-missing",
            ],
        })
    return {
        "schema_version": 1,
        "id": "rabbitmq-reference-system-results-v1",
        "atlas_id": "rabbitmq-reference-atlas",
        "generated_at": GENERATED_AT,
        "status": "incomplete-no-integrated-runtime-evidence",
        "manifest": "reference-system/manifest.yaml",
        "manifest_digest": sha_file(ROOT / "reference-system/manifest.yaml"),
        "reuse_policy": "forbidden-for-behavior-completion",
        "summary": {"scenarios": 10, "executed": 0, "passed": 0, "gaps": 10},
        "scenarios": rows,
    }


def build() -> tuple[dict, dict[str, dict]]:
    plan = load_yaml(ROOT / "verification.plan.yaml")
    inventory = load_yaml(ROOT / "surface.inventory.yaml")
    manifest = load_yaml(ROOT / "reference-system/manifest.yaml")
    decisions = load_json(ROOT / "authority/reviews/decisions.json")
    review_queue = load_json(ROOT / "authority/review-queue.snapshot.json")
    artifacts = authority_artifacts(inventory)
    inventory_by_behavior = {item["behavior_id"]: item for item in inventory["items"]}
    records = {record["id"]: record for record in (
        load_json(path) for path in sorted((ROOT / "evidence").glob("definitive.*.evidence.json"))
    )}
    reference_results = build_reference_results(manifest)
    reference_by_scenario = {item["scenario"]: item for item in reference_results["scenarios"]}
    proofs: dict[str, dict] = {}
    evidence_row_use: defaultdict[str, list[str]] = defaultdict(list)

    for row in plan["rows"]:
        item = inventory_by_behavior[row["behavior_id"]]
        edge = reference_edge(item, artifacts)
        decision = human_decision_for(row["behavior_id"], decisions)
        authority_atomic = decision is not None and review_queue["summary"].get("promoted_atomic_behaviors", 0) > 0
        record, raw, evidence_gaps = exact_behavior_evidence(row, item, records)
        if record:
            evidence_row_use[record["id"]].append(f"{row['behavior_id']}:{row['scenario']}")
        identity, identity_gaps = identities(record)
        channels, artifact_gaps = channel_bindings(record, raw)
        behavior_specific = (
            row["applicability"] == "required"
            and record is not None
            and raw is not None
            and not evidence_gaps
            and not identity_gaps
            and any(channels[name]["artifacts"] for name in CHANNELS)
        )
        gaps = [*evidence_gaps, *identity_gaps, *artifact_gaps]
        if row["applicability"] == "required" and not authority_atomic:
            gaps.append("authority.atomic-human-promotion-missing")
        if row["applicability"] == "required" and not behavior_specific:
            gaps.append("proof.behavior-specific-runtime-missing")
        completion_eligible = bool(row["applicability"] == "required" and authority_atomic and behavior_specific and not gaps)
        status = "not-applicable-contract" if row["applicability"] != "required" else ("bounded-behavior-runtime-proof" if behavior_specific else "behavior-specific-gap")
        proof = {
            "schema_version": 1,
            "id": f"proof.behavior.{row['behavior_id']}.{row['scenario']}",
            "atlas_id": "rabbitmq-reference-atlas",
            "generated_at": GENERATED_AT,
            "behavior_id": row["behavior_id"],
            "target_id": row["target_id"],
            "scenario": row["scenario"],
            "applicability": row["applicability"],
            "status": status,
            "runtime_profile": row["profile"],
            "proof_obligation_id": row["proof_obligation_id"],
            "authority_binding": {
                "reference_edge": edge,
                "human_review_decision": decision,
                "authority_atomic_behavior": authority_atomic,
            },
            "behavior_evidence": {
                "evidence_id": record["id"] if record else None,
                "evidence_record": f"evidence/{record['id']}.evidence.json" if record else None,
                "behavior_specific": behavior_specific,
                "aggregate_evidence_allowed": False,
            },
            "identity": identity,
            "artifact_channels": channels,
            "integrated_reference": {
                "manifest": "reference-system/manifest.yaml",
                "result": "reference-system/results.json",
                "scenario_id": reference_by_scenario[row["scenario"]]["id"],
                "status": reference_by_scenario[row["scenario"]]["status"],
                "behavior_completion_reuse_allowed": False,
            },
            "closure": {
                "dedicated_row": True,
                "dedicated_artifact": True,
                "behavior_specific_runtime_proof": behavior_specific,
                "broker_client_version_runtime_identity": not identity_gaps and record is not None,
                "authority_atomic_behavior": authority_atomic,
                "completion_eligible": completion_eligible,
            },
            "gap_ids": sorted(set(gaps)),
        }
        path = f"evidence/scenarios/behaviors/{safe_behavior_path(row['behavior_id'])}/{row['scenario']}.proof.json"
        proofs[path] = proof

    duplicate_use = {key: value for key, value in evidence_row_use.items() if len(value) != 1}
    files = [{
        "id": proof["id"], "behavior_id": proof["behavior_id"], "scenario": proof["scenario"],
        "path": path, "digest": sha_bytes(canonical(proof).encode()), "status": proof["status"],
        "behavior_specific_runtime": proof["closure"]["behavior_specific_runtime_proof"],
        "completion_eligible": proof["closure"]["completion_eligible"],
    } for path, proof in sorted(proofs.items())]
    required = [proof for proof in proofs.values() if proof["applicability"] == "required"]
    specific = [proof for proof in required if proof["closure"]["behavior_specific_runtime_proof"]]
    eligible = [proof for proof in required if proof["closure"]["completion_eligible"]]
    by_profile: dict[str, dict[str, int]] = {}
    for profile in sorted({proof["runtime_profile"] for proof in required}):
        rows = [proof for proof in required if proof["runtime_profile"] == profile]
        by_profile[profile] = {
            "required": len(rows),
            "behavior_specific_runtime": sum(item["closure"]["behavior_specific_runtime_proof"] for item in rows),
            "completion_eligible": sum(item["closure"]["completion_eligible"] for item in rows),
        }
    by_scenario = {}
    for scenario in SCENARIOS:
        rows = [proof for proof in required if proof["scenario"] == scenario]
        by_scenario[scenario] = {
            "required": len(rows),
            "behavior_specific_runtime": sum(item["closure"]["behavior_specific_runtime_proof"] for item in rows),
            "completion_eligible": sum(item["closure"]["completion_eligible"] for item in rows),
        }
    source_paths = [
        "verification.plan.yaml", "surface.inventory.yaml", "authority/reviews/decisions.json",
        "authority/review-queue.snapshot.json", "reference-system/manifest.yaml", "scripts/scenario_proof.py",
    ]
    index = {
        "schema_version": 1,
        "id": "rabbitmq-scenario-proof-matrix-v1",
        "atlas_id": "rabbitmq-reference-atlas",
        "generated_at": GENERATED_AT,
        "status": "incomplete-authority-atomic-and-runtime-closure",
        "denominator": "206-authority-reference-edge-behaviors-x-10-scenarios",
        "source_digests": {path: sha_file(ROOT / path) for path in source_paths},
        "summary": {
            "behaviors": len(inventory["items"]),
            "scenarios": len(SCENARIOS),
            "rows": len(proofs),
            "required_rows": len(required),
            "not_applicable_rows": len(proofs) - len(required),
            "dedicated_artifacts": len(files),
            "behavior_specific_runtime_rows": len(specific),
            "behavior_specific_gap_rows": len(required) - len(specific),
            "authority_atomic_rows": sum(item["closure"]["authority_atomic_behavior"] for item in required),
            "completion_eligible_rows": len(eligible),
            "integrated_scenarios": 10,
            "integrated_executed": reference_results["summary"]["executed"],
            "integrated_passed": reference_results["summary"]["passed"],
            "duplicate_behavior_evidence_bindings": len(duplicate_use),
        },
        "authority_review": {
            "queue_id": review_queue["queue_id"],
            "human_reviewed": review_queue["summary"]["human_reviewed"],
            "promoted_atomic_behaviors": review_queue["summary"]["promoted_atomic_behaviors"],
        },
        "by_profile": by_profile,
        "by_scenario": by_scenario,
        "reference_system": {
            "manifest": "reference-system/manifest.yaml",
            "results": "reference-system/results.json",
            "reuse_for_behavior_completion": False,
        },
        "files": files,
        "completion_limits": [
            "統合Reference Systemの成功をBehavior固有Runtime Proofへ流用しない。",
            "Broker、Client version、Runtime identityが揃わないRequired rowはCompletion対象外。",
            "packet、log、metricはArtifactまたは明示gapを各rowへ記録する。",
            "Authority raw anchorの人手decisionとAtomic behavior昇格がないrowはCompletion対象外。",
        ],
    }
    return {"index": index, "reference_results": reference_results, "duplicates": duplicate_use}, proofs


def validate_built(bundle: dict, proofs: dict[str, dict]) -> list[str]:
    errors = []
    index, reference = bundle["index"], bundle["reference_results"]
    if reference["summary"] != {"scenarios": 10, "executed": 0, "passed": 0, "gaps": 10}:
        errors.append("Reference Systemの未実行10 Scenario集計が不正です。")
    if [item["scenario"] for item in reference["scenarios"]] != SCENARIOS:
        errors.append("Reference SystemのScenario集合または順序が不正です。")
    if any(item["completion_eligible"] or not item["gap_ids"] for item in reference["scenarios"]):
        errors.append("未実行Reference ScenarioがCompletionまたはgapなしになっています。")
    if bundle["duplicates"]:
        errors.append(f"Behavior Evidenceが複数rowで流用されています: {bundle['duplicates']}")
    if len(proofs) != 2060 or index["summary"]["required_rows"] != 951:
        errors.append("Behavior×Scenario denominatorが非後退分母と一致しません。")
    for path, proof in proofs.items():
        if proof["integrated_reference"]["behavior_completion_reuse_allowed"]:
            errors.append(f"統合ProofのBehavior流用が有効です: {path}")
        for channel in CHANNELS:
            contract = proof["artifact_channels"].get(channel)
            if not contract or (not contract["artifacts"] and not contract["gap_ids"]):
                errors.append(f"Artifact channelにArtifactもgapもありません: {path}:{channel}")
        closure = proof["closure"]
        if closure["completion_eligible"] and not (
            closure["authority_atomic_behavior"]
            and closure["behavior_specific_runtime_proof"]
            and closure["broker_client_version_runtime_identity"]
        ):
            errors.append(f"Completion eligibilityが必須bindingを迂回しています: {path}")
        if not closure["authority_atomic_behavior"] and closure["completion_eligible"]:
            errors.append(f"Authority atomic bindingなしでCompletion eligibleです: {path}")
        if proof["applicability"] == "required" and not proof["gap_ids"] and not closure["completion_eligible"]:
            errors.append(f"未完了Required rowに明示gapがありません: {path}")
    if index["authority_review"]["promoted_atomic_behaviors"] == 0 and index["summary"]["completion_eligible_rows"] != 0:
        errors.append("人手Authority昇格前のCompletion eligible rowは0でなければなりません。")
    return errors


def generate() -> dict:
    bundle, proofs = build()
    errors = validate_built(bundle, proofs)
    if errors:
        raise SystemExit("\n".join(errors))
    if BEHAVIOR_ROOT.exists():
        shutil.rmtree(BEHAVIOR_ROOT)
    for path, proof in proofs.items():
        destination = ROOT / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(canonical(proof), encoding="utf-8")
    REFERENCE_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_RESULT_PATH.write_text(canonical(bundle["reference_results"]), encoding="utf-8")
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(canonical(bundle["index"]), encoding="utf-8")
    return bundle["index"]


def validate_files() -> list[str]:
    bundle, proofs = build()
    errors = validate_built(bundle, proofs)
    expected_paths = sorted(proofs)
    actual_paths = sorted(relative(path) for path in BEHAVIOR_ROOT.rglob("*.proof.json")) if BEHAVIOR_ROOT.exists() else []
    if actual_paths != expected_paths:
        errors.append(f"Scenario Proof file集合が不一致です: expected={len(expected_paths)} actual={len(actual_paths)}")
    for path, expected in proofs.items():
        actual_path = ROOT / path
        if not actual_path.is_file() or actual_path.read_text(encoding="utf-8") != canonical(expected):
            errors.append(f"Scenario Proofがstaleです: {path}")
    for path, expected in ((INDEX_PATH, bundle["index"]), (REFERENCE_RESULT_PATH, bundle["reference_results"])):
        if not path.is_file() or path.read_text(encoding="utf-8") != canonical(expected):
            errors.append(f"Scenario Proof index/resultがstaleです: {relative(path)}")
    if INDEX_PATH.is_file():
        stored = load_json(INDEX_PATH)
        for descriptor in stored.get("files", []):
            artifact = ROOT / descriptor["path"]
            if not artifact.is_file() or sha_file(artifact) != descriptor["digest"]:
                errors.append(f"Scenario Proof digest mismatch: {descriptor['path']}")
    return errors
