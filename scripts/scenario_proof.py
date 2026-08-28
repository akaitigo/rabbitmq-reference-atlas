#!/usr/bin/env python3
"""RabbitMQ Reference SystemとBehavior固有Scenario Proofを分離して構築・検証する。"""

from __future__ import annotations

import hashlib
import json
import re
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
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def required_variants(row: dict, inventory_item: dict, contract: dict) -> tuple[list[str], list[str]]:
    runtime = list(contract["profile_variants"].get(row["profile"], []))
    gaps = []
    if not runtime:
        gaps.append("variant.runtime-profile-inventory-missing")
    semantic = []
    if "decision-comparison" in inventory_item["surface_ids"]:
        semantic = list(contract.get("semantic_variants", {}).get(row["behavior_id"], []))
        if len(semantic) < 2:
            gaps.append("variant.semantic-inventory-missing")
    variants = ([f"{runtime_id}--semantic-{semantic_id}" for runtime_id in runtime for semantic_id in semantic]
                if semantic else runtime)
    if len(variants) != len(set(variants)):
        gaps.append("variant.inventory-duplicate")
    return variants, gaps


def exact_file_binding(binding: Any, prefix: str, gaps: list[str]) -> Path | None:
    if not isinstance(binding, dict):
        gaps.append(f"{prefix}.missing")
        return None
    name, digest = binding.get("path"), binding.get("digest")
    if not isinstance(name, str) or not name or not isinstance(digest, str) or not SHA256.match(digest):
        gaps.append(f"{prefix}.binding-invalid")
        return None
    candidate = (ROOT / name).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        gaps.append(f"{prefix}.path-outside-repository")
        return None
    if not candidate.is_file() or sha_file(candidate) != digest:
        gaps.append(f"{prefix}.digest-mismatch")
        return None
    return candidate


def dedicated_runtime_proof(row: dict, inventory_item: dict, contract: dict,
                            forbidden_artifact_digests: set[str] | None = None) -> tuple[dict, list[str], list[str]]:
    behavior_path = safe_behavior_path(row["behavior_id"])
    report_name = f"evidence/scenario-runtime/{behavior_path}/{row['scenario']}.runtime.json"
    expected_variants, variant_gaps = required_variants(row, inventory_item, contract)
    scenario_gaps = list(variant_gaps)
    artifact_uses: list[str] = []
    forbidden_artifact_digests = forbidden_artifact_digests or set()
    report_path = ROOT / report_name
    report = load_json(report_path) if report_path.is_file() else None
    variant_proofs = []
    report_binding = {"path": report_name, "present": report is not None, "digest": sha_file(report_path) if report else None}

    if row["applicability"] != "required":
        return {
            "report": report_binding, "required_variants": expected_variants, "variant_proofs": [],
            "requirements": {}, "scenario_gap_closed": False,
        }, ["scenario.not-applicable-by-surface-contract"], []
    if not report:
        scenario_gaps.append("runtime.dedicated-report-missing")
        scenario_gaps.append("variant.execution-missing")
        for variant in expected_variants:
            variant_proofs.append({
                "id": variant, "attempts": None, "retries": None,
                "broker": None, "client": None, "runtime": None,
                "oracle": None, "source": None, "harness": None,
                "artifact_channels": {
                    channel: {"artifact": None, "gap_ids": [f"artifact.{variant}.{channel}.missing"]}
                    for channel in CHANNELS
                },
            })
            scenario_gaps.extend(f"artifact.{variant}.{channel}.missing" for channel in CHANNELS)
        scenario_gaps.extend([
            "execution.attempts-not-one", "execution.retries-not-zero", "oracle.variant-pass-missing",
            "digest.source-harness-missing", "identity.broker-client-runtime-missing",
        ])
    else:
        for field, expected in (("behavior_id", row["behavior_id"]), ("authority_surface_id", inventory_item["authority_surface_id"]),
                                ("scenario", row["scenario"]), ("runtime_profile", row["profile"])):
            if report.get(field) != expected:
                scenario_gaps.append(f"runtime.report-{field}-mismatch")
        if report.get("status") != "passed":
            scenario_gaps.append("runtime.report-not-passed")
        if report.get("attempts") != 1:
            scenario_gaps.append("execution.attempts-not-one")
        if report.get("retries") != 0:
            scenario_gaps.append("execution.retries-not-zero")
        source = report.get("source")
        harness = report.get("harness")
        exact_file_binding(source, "digest.source", scenario_gaps)
        exact_file_binding(harness, "digest.harness", scenario_gaps)
        raw_variants = report.get("variants") if isinstance(report.get("variants"), list) else []
        ids = [item.get("id") for item in raw_variants if isinstance(item, dict)]
        if sorted(ids) != sorted(expected_variants) or len(ids) != len(set(ids)):
            scenario_gaps.append("variant.executed-set-mismatch")
        by_id = {item.get("id"): item for item in raw_variants if isinstance(item, dict) and isinstance(item.get("id"), str)}
        expected_prefix = f"evidence/scenario-runtime/artifacts/{behavior_path}/{row['scenario']}/"
        for variant in expected_variants:
            value = by_id.get(variant, {})
            local_gaps = []
            if value.get("attempts") != 1:
                local_gaps.append(f"execution.{variant}.attempts-not-one")
            if value.get("retries") != 0:
                local_gaps.append(f"execution.{variant}.retries-not-zero")
            broker, client, runtime = value.get("broker"), value.get("client"), value.get("runtime")
            if not isinstance(broker, dict) or not all((broker.get("runtime_kind") == "actual-broker",
                                                       broker.get("product") == contract["identity"]["broker_product"],
                                                       broker.get("version") == contract["identity"]["broker_version"],
                                                       broker.get("image_digest") == contract["identity"]["broker_image_digest"])):
                local_gaps.append(f"identity.{variant}.broker-incomplete")
            if (not isinstance(client, dict) or client.get("runtime_kind") != "actual-client"
                    or not all((client.get("name"), client.get("version")))
                    or not SHA256.match(str(client.get("source_digest", "")))
                    or not isinstance(source, dict) or client.get("source_digest") != source.get("digest")):
                local_gaps.append(f"identity.{variant}.client-incomplete")
            if not isinstance(runtime, dict) or not all((runtime.get("profile") == row["profile"], runtime.get("platform"), runtime.get("execution_id"))):
                local_gaps.append(f"identity.{variant}.runtime-incomplete")
            oracle = value.get("oracle")
            if (not isinstance(oracle, dict) or not oracle.get("id")
                    or not isinstance(oracle.get("assertions"), list) or not oracle["assertions"]
                    or oracle.get("passed") is not True):
                local_gaps.append(f"oracle.{variant}.pass-missing")
            if value.get("source") != source:
                local_gaps.append(f"digest.{variant}.source-binding-mismatch")
            if value.get("harness") != harness:
                local_gaps.append(f"digest.{variant}.harness-binding-mismatch")
            channels = value.get("artifact_channels") if isinstance(value.get("artifact_channels"), dict) else {}
            rendered_channels = {}
            for channel in CHANNELS:
                binding = channels.get(channel)
                channel_gaps = []
                bound_path = exact_file_binding(binding, f"artifact.{variant}.{channel}", channel_gaps)
                if not isinstance(binding, dict) or not str(binding.get("path", "")).startswith(f"{expected_prefix}{variant}/{channel}."):
                    channel_gaps.append(f"artifact.{variant}.{channel}.not-dedicated")
                if isinstance(binding, dict) and (binding.get("channel") != channel or not binding.get("media_type")):
                    channel_gaps.append(f"artifact.{variant}.{channel}.metadata-incomplete")
                if isinstance(binding, dict) and binding.get("digest") in forbidden_artifact_digests:
                    channel_gaps.append(f"artifact.{variant}.{channel}.other-evidence-metadata-reused")
                if isinstance(binding, dict) and bound_path and binding.get("size_bytes") != bound_path.stat().st_size:
                    channel_gaps.append(f"artifact.{variant}.{channel}.size-mismatch")
                if isinstance(binding, dict) and isinstance(binding.get("path"), str):
                    artifact_uses.append(binding["path"])
                rendered_channels[channel] = {"artifact": binding if isinstance(binding, dict) else None,
                                              "gap_ids": sorted(set(channel_gaps))}
                local_gaps.extend(channel_gaps)
            variant_proofs.append({
                "id": variant, "attempts": value.get("attempts"), "retries": value.get("retries"),
                "broker": broker, "client": client, "runtime": runtime, "oracle": oracle,
                "source": value.get("source"), "harness": value.get("harness"),
                "artifact_channels": rendered_channels,
            })
            scenario_gaps.extend(local_gaps)

    requirements = {
        "dedicated_surface_scenario_report": report is not None and not any(gap.startswith("runtime.report") or gap == "runtime.dedicated-report-missing" for gap in scenario_gaps),
        "all_variants_driven": bool(expected_variants) and not any(gap.startswith("variant.") for gap in scenario_gaps),
        "attempts_one_retry_zero": not any(gap.startswith("execution.") for gap in scenario_gaps),
        "oracle_pass_per_variant": not any(gap.startswith("oracle.") for gap in scenario_gaps),
        "source_harness_digest_bound": not any(gap.startswith("digest.") for gap in scenario_gaps),
        "broker_client_runtime_identity": not any(gap.startswith("identity.") for gap in scenario_gaps),
        "packet_log_metric_dedicated": not any(gap.startswith("artifact.") for gap in scenario_gaps),
    }
    scenario_gap_closed = bool(report and all(requirements.values()) and not scenario_gaps)
    return {
        "report": report_binding, "required_variants": expected_variants, "variant_proofs": variant_proofs,
        "requirements": requirements, "scenario_gap_closed": scenario_gap_closed,
    }, sorted(set(scenario_gaps)), artifact_uses


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
    closure_contract = load_yaml(ROOT / "scenario-closure.yaml")
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
    dedicated_artifact_use: defaultdict[str, list[str]] = defaultdict(list)
    forbidden_artifact_digests = {
        record.get("artifact", {}).get("digest") for record in records.values()
        if record.get("artifact", {}).get("digest")
    }

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
        legacy_observation = (
            row["applicability"] == "required"
            and record is not None
            and raw is not None
            and not evidence_gaps
            and not identity_gaps
            and any(channels[name]["artifacts"] for name in CHANNELS)
        )
        dedicated, scenario_gaps, artifact_uses = dedicated_runtime_proof(
            row, item, closure_contract, forbidden_artifact_digests)
        proof_key = f"{row['behavior_id']}:{row['scenario']}"
        for artifact in artifact_uses:
            dedicated_artifact_use[artifact].append(proof_key)
        completion_gaps = []
        if row["applicability"] == "required" and not authority_atomic:
            completion_gaps.append("authority.atomic-human-promotion-missing")
        completion_eligible = bool(row["applicability"] == "required" and authority_atomic and dedicated["scenario_gap_closed"])
        status = ("not-applicable-contract" if row["applicability"] != "required"
                  else "scenario-gap-closed" if dedicated["scenario_gap_closed"]
                  else "legacy-runtime-observation-gap-open" if legacy_observation
                  else "scenario-gap-open")
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
            "legacy_observation": {
                "evidence_id": record["id"] if record else None,
                "evidence_record": f"evidence/{record['id']}.evidence.json" if record else None,
                "behavior_specific": legacy_observation,
                "identity": identity,
                "artifact_channels": channels,
                "observation_gap_ids": sorted(set([*evidence_gaps, *identity_gaps, *artifact_gaps])),
                "counts_toward_scenario_gap_closure": False,
                "aggregate_evidence_allowed": False,
            },
            "dedicated_runtime": dedicated,
            "integrated_reference": {
                "manifest": "reference-system/manifest.yaml",
                "result": "reference-system/results.json",
                "scenario_id": reference_by_scenario[row["scenario"]]["id"],
                "status": reference_by_scenario[row["scenario"]]["status"],
                "behavior_completion_reuse_allowed": False,
            },
            "closure": {
                "dedicated_row": True,
                **dedicated["requirements"],
                "artifact_metadata_exclusive": True,
                "scenario_gap_closed": dedicated["scenario_gap_closed"],
                "authority_atomic_behavior": authority_atomic,
                "completion_eligible": completion_eligible,
            },
            "scenario_gap_ids": scenario_gaps,
            "completion_gap_ids": sorted(set(completion_gaps)),
            "gap_ids": sorted(set([*scenario_gaps, *completion_gaps])),
        }
        path = f"evidence/scenarios/behaviors/{safe_behavior_path(row['behavior_id'])}/{row['scenario']}.proof.json"
        proofs[path] = proof

    duplicate_use = {key: value for key, value in evidence_row_use.items() if len(value) != 1}
    duplicate_artifacts = {key: value for key, value in dedicated_artifact_use.items() if len(value) != 1}
    files = [{
        "id": proof["id"], "behavior_id": proof["behavior_id"], "scenario": proof["scenario"],
        "path": path, "digest": sha_bytes(canonical(proof).encode()), "status": proof["status"],
        "legacy_runtime_observation": proof["legacy_observation"]["behavior_specific"],
        "dedicated_runtime_report": proof["dedicated_runtime"]["report"]["present"],
        "scenario_gap_closed": proof["closure"]["scenario_gap_closed"],
        "completion_eligible": proof["closure"]["completion_eligible"],
    } for path, proof in sorted(proofs.items())]
    required = [proof for proof in proofs.values() if proof["applicability"] == "required"]
    observed = [proof for proof in required if proof["legacy_observation"]["behavior_specific"]]
    reported = [proof for proof in required if proof["dedicated_runtime"]["report"]["present"]]
    closed = [proof for proof in required if proof["closure"]["scenario_gap_closed"]]
    eligible = [proof for proof in required if proof["closure"]["completion_eligible"]]
    by_profile: dict[str, dict[str, int]] = {}
    for profile in sorted({proof["runtime_profile"] for proof in required}):
        rows = [proof for proof in required if proof["runtime_profile"] == profile]
        by_profile[profile] = {
            "required": len(rows),
            "legacy_runtime_observation": sum(item["legacy_observation"]["behavior_specific"] for item in rows),
            "dedicated_runtime_report": sum(item["dedicated_runtime"]["report"]["present"] for item in rows),
            "scenario_gap_closed": sum(item["closure"]["scenario_gap_closed"] for item in rows),
            "completion_eligible": sum(item["closure"]["completion_eligible"] for item in rows),
        }
    by_scenario = {}
    for scenario in SCENARIOS:
        rows = [proof for proof in required if proof["scenario"] == scenario]
        by_scenario[scenario] = {
            "required": len(rows),
            "legacy_runtime_observation": sum(item["legacy_observation"]["behavior_specific"] for item in rows),
            "dedicated_runtime_report": sum(item["dedicated_runtime"]["report"]["present"] for item in rows),
            "scenario_gap_closed": sum(item["closure"]["scenario_gap_closed"] for item in rows),
            "completion_eligible": sum(item["closure"]["completion_eligible"] for item in rows),
        }
    source_paths = [
        "verification.plan.yaml", "surface.inventory.yaml", "authority/reviews/decisions.json",
        "authority/review-queue.snapshot.json", "reference-system/manifest.yaml", "scenario-closure.yaml",
        "scripts/scenario_proof.py",
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
            "dedicated_proof_files": len(files),
            "legacy_runtime_observation_rows": len(observed),
            "dedicated_runtime_report_rows": len(reported),
            "scenario_gap_closed_rows": len(closed),
            "scenario_gap_open_rows": len(required) - len(closed),
            "authority_atomic_rows": sum(item["closure"]["authority_atomic_behavior"] for item in required),
            "completion_eligible_rows": len(eligible),
            "integrated_scenarios": 10,
            "integrated_executed": reference_results["summary"]["executed"],
            "integrated_passed": reference_results["summary"]["passed"],
            "duplicate_behavior_evidence_bindings": len(duplicate_use),
            "duplicate_dedicated_artifact_bindings": len(duplicate_artifacts),
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
        "scenario_gap_closure": {
            "manifest": "scenario-closure.yaml",
            "closure_unit": closure_contract["closure_unit"],
            "attempts": closure_contract["execution"]["attempts"],
            "retries": closure_contract["execution"]["retries"],
            "required_channels": closure_contract["proof"]["artifact_channels_per_variant"],
            "profile_variants": closure_contract["profile_variants"],
            "semantic_variant_inventory": closure_contract["semantic_variants"],
            "integrated_system_reuse": closure_contract["proof"]["integrated_system_reuse"],
            "other_evidence_metadata_reuse": closure_contract["proof"]["other_evidence_metadata_reuse"],
        },
        "files": files,
        "completion_limits": [
            "統合Reference Systemの成功をBehavior固有Runtime Proofへ流用しない。",
            "専用実Broker/ClientでSurface、Scenario、全Runtime/Semantic Variantをattempts=1、retries=0で実行する。",
            "各VariantへOracle、Source/Harness digest、Runtime identity、専用packet/log/metric Artifactを要求する。",
            "統合Systemまたは別EvidenceのArtifact path/metadataをScenario gap Closureへ流用しない。",
            "Authority raw anchorの人手decisionとAtomic behavior昇格がないrowはCompletion対象外。",
        ],
    }
    return {"index": index, "reference_results": reference_results,
            "duplicates": duplicate_use, "duplicate_artifacts": duplicate_artifacts}, proofs


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
    if bundle["duplicate_artifacts"]:
        errors.append(f"専用Artifactが複数row/channelで流用されています: {bundle['duplicate_artifacts']}")
    if len(proofs) != 2060 or index["summary"]["required_rows"] != 951:
        errors.append("Behavior×Scenario denominatorが非後退分母と一致しません。")
    for path, proof in proofs.items():
        if proof["integrated_reference"]["behavior_completion_reuse_allowed"]:
            errors.append(f"統合ProofのBehavior流用が有効です: {path}")
        closure = proof["closure"]
        if proof["legacy_observation"]["counts_toward_scenario_gap_closure"]:
            errors.append(f"Legacy observationがScenario gap Closureへ算入されています: {path}")
        variants = proof["dedicated_runtime"]["variant_proofs"]
        if proof["applicability"] == "required":
            if [item["id"] for item in variants] != proof["dedicated_runtime"]["required_variants"]:
                errors.append(f"専用Variant集合が契約と一致しません: {path}")
            for variant in variants:
                for channel in CHANNELS:
                    contract = variant["artifact_channels"].get(channel)
                    if not contract or (not contract["artifact"] and not contract["gap_ids"]):
                        errors.append(f"専用Artifact channelにArtifactもgapもありません: {path}:{variant['id']}:{channel}")
        strict_keys = {
            "dedicated_surface_scenario_report", "all_variants_driven", "attempts_one_retry_zero",
            "oracle_pass_per_variant", "source_harness_digest_bound", "broker_client_runtime_identity",
            "packet_log_metric_dedicated", "artifact_metadata_exclusive",
        }
        if closure["scenario_gap_closed"] and (
            not all(closure.get(key) is True for key in strict_keys) or proof["scenario_gap_ids"]
        ):
            errors.append(f"Scenario gap Closureが専用実行条件を迂回しています: {path}")
        if closure["completion_eligible"] and not (closure["authority_atomic_behavior"] and closure["scenario_gap_closed"]):
            errors.append(f"Completion eligibilityが必須bindingを迂回しています: {path}")
        if not closure["authority_atomic_behavior"] and closure["completion_eligible"]:
            errors.append(f"Authority atomic bindingなしでCompletion eligibleです: {path}")
        if proof["applicability"] == "required" and not proof["scenario_gap_ids"] and not closure["scenario_gap_closed"]:
            errors.append(f"未Closure Required rowに明示Scenario gapがありません: {path}")
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
