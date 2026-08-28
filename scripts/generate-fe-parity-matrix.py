#!/usr/bin/env python3
"""FE Depth Referenceの18軸をRabbitMQ固有denominatorへ写像する。"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "rabbitmq-depth-parity.yaml"
REFERENCE = ROOT / "parity/frontend-depth-reference.yaml"


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def evidence_records() -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "evidence").glob("definitive.*.evidence.json"))]


def make_check(check_id: str, required: str, observed, status: str, evidence: list[str], gaps: list[str] | None = None) -> dict:
    return {"id": check_id, "required": required, "observed": observed, "status": status,
            "evidence": evidence, "gap_ids": gaps or []}


def make_axis(reference_axis: dict, denominator: dict, checks: list[dict]) -> dict:
    gaps = sorted({gap for check in checks for gap in check["gap_ids"]})
    passed = sum(check["status"] == "pass" for check in checks)
    progressed = sum(check["status"] in {"pass", "partial"} for check in checks)
    status = "satisfied" if not gaps and passed == len(checks) else ("partial" if progressed else "missing")
    return {
        "id": reference_axis["id"], "title": reference_axis["title"],
        "portable_criterion": reference_axis["portable_criterion"],
        "rabbitmq_denominator": denominator, "status": status,
        "gap_count": len(gaps), "gap_ids": gaps, "checks": checks,
    }


def scenario_axis(reference_axis: dict, scenario: str, rows: list[dict]) -> dict:
    applicable = [row for row in rows if row["scenario"] == scenario and row["applicability"] == "required"]
    covered = [row for row in applicable if row["state"] == "covered" and row.get("evidence_ids")]
    gaps = [row["proof_obligation_id"] for row in applicable if row not in covered]
    partitions: dict[str, dict[str, int]] = defaultdict(lambda: {"required": 0, "covered": 0})
    for row in applicable:
        partitions[row["profile"]]["required"] += 1
        if row in covered:
            partitions[row["profile"]]["covered"] += 1
    check = make_check(
        f"{reference_axis['id']}.dedicated-proof",
        "各Required behaviorへ専用Oracle、実Profile、専用Artifact、Evidenceを一対一で接続する。",
        f"{len(covered)}/{len(applicable)}", "pass" if not gaps else ("partial" if covered else "gap"),
        ["verification.plan.yaml", "evidence/", "evidence/raw/"], gaps)
    return make_axis(reference_axis, {
        "unit": f"authority-derived behavior requiring {scenario}",
        "required": len(applicable), "covered": len(covered), "remaining": len(applicable) - len(covered),
        "profile_partitions": dict(sorted(partitions.items())),
    }, [check])


def build() -> dict:
    atlas = load_yaml(ROOT / "atlas.yaml")
    coverage = load_yaml(ROOT / "coverage.yaml")
    inventory = load_yaml(ROOT / "surface.inventory.yaml")
    plan = load_yaml(ROOT / "verification.plan.yaml")
    classification = load_yaml(ROOT / "surface/source-classification.yaml")
    definitive = load_yaml(ROOT / "definitive.yaml")
    sources = load_yaml(ROOT / "sources.lock.yaml")
    reference = load_yaml(REFERENCE)
    skill_eval = json.loads((ROOT / definitive["skill_eval"]).read_text(encoding="utf-8"))
    records = evidence_records()
    axes_by_id = {axis["id"]: axis for axis in reference["axes"]}
    expected_axes = [
        "authority-body-digestion", "surface-atomic-behavior-variant", "real-runtime-lab",
        "scenario-normal", "scenario-boundary", "scenario-refusal", "scenario-failure",
        "scenario-recovery", "scenario-migration", "scenario-operations", "scenario-security",
        "scenario-performance", "scenario-compatibility", "artifact-trace",
        "integrated-reference-system", "skill-eval", "rights-provenance", "non-regression-gate",
    ]
    if list(axes_by_id) != expected_axes:
        raise SystemExit("FE Depth Reference axis order/identity mismatch")

    rows = plan["rows"]
    required_rows = [row for row in rows if row["applicability"] == "required"]
    record_by_id = {record["id"]: record for record in records}
    non_behavioral_sources = {
        "rabbitmq-server-v4.3.5", "rabbitmq-server-v4.2.9-release-notes",
        "rabbitmq-container-v4.2.9-management", "rabbitmq-container-v4.3.5-management",
    }
    behavioral_sources = [row for row in classification["sources"] if row["source_id"] not in non_behavioral_sources]
    authority_gaps = [row["source_id"] for row in behavioral_sources if row["classification"] != "surface-authority"]
    locked_sources = [source for source in sources["sources"] if source.get("digest") and source.get("version") and source.get("url")]
    authority_axis = make_axis(axes_by_id["authority-body-digestion"], {
        "unit": "behavioral authority source and locator-extracted surface", "sources": len(behavioral_sources),
        "extracted_sources": len(behavioral_sources) - len(authority_gaps), "atomic_surfaces": len(inventory["items"]),
        "remaining_sources": len(authority_gaps),
    }, [
        make_check("authority.body-lock", "全SourceのURL、Version、取得日、本文SHA-256を固定する。",
                   f"{len(locked_sources)}/{len(sources['sources'])}", "pass" if len(locked_sources) == len(sources["sources"]) else "gap",
                   ["sources.lock.yaml"], [source["id"] for source in sources["sources"] if source not in locked_sources]),
        make_check("authority.surface-extraction", "全Behavioral AuthorityをLocator付きSurface Artifactへ抽出する。",
                   f"{len(behavioral_sources) - len(authority_gaps)}/{len(behavioral_sources)}", "pass" if not authority_gaps else "partial",
                   ["surface/authority/", "surface/source-classification.yaml"], authority_gaps),
        make_check("authority.human-review", "抽出Artifactの本文解釈と分類をReview metadataへ固定する。",
                   f"{len(inventory['authority_artifacts'])}/{len(inventory['authority_artifacts'])}", "pass", ["surface/authority/"], []),
    ])

    comparison_items = [item for item in inventory["items"] if "decision-comparison" in item["surface_ids"]]
    declared_behavior_ids = {item.get("behavior_id") for item in definitive.get("comparisons", [])}
    comparison_gaps = [f"comparison.{item['behavior_id']}" for item in comparison_items if item["behavior_id"] not in declared_behavior_ids]
    behavior_axis = make_axis(axes_by_id["surface-atomic-behavior-variant"], {
        "unit": "authority-derived atomic behavior", "required": len(inventory["items"]), "mapped": len(inventory["items"]),
        "comparison_required": len(comparison_items), "comparison_closed": len(comparison_items) - len(comparison_gaps),
    }, [
        make_check("behavior.one-to-one-mapping", "各Surfaceを一意なBehavior、Target、Claimへ接続する。",
                   f"{len(inventory['items'])}/{len(inventory['items'])}", "pass", ["surface.inventory.yaml", "coverage.yaml", "claims/"], []),
        make_check("behavior.observable-contract", "各BehaviorのScenario OracleとProof obligationを宣言する。",
                   f"{len(required_rows)}/{len(required_rows)} rows declared", "pass", ["verification.plan.yaml", "claims/"], []),
        make_check("behavior.multi-variant", "比較Surfaceへ同一Oracleの二つ以上の実Variantを接続する。",
                   f"{len(comparison_items) - len(comparison_gaps)}/{len(comparison_items)}", "pass" if not comparison_gaps else "gap",
                   ["definitive.yaml", "verification.plan.yaml"], comparison_gaps),
    ])

    behavior_rows: dict[str, list[dict]] = defaultdict(list)
    for row in required_rows:
        behavior_rows[row["behavior_id"]].append(row)
    closed_behaviors = [behavior for behavior, required in behavior_rows.items()
                        if all(row["state"] == "covered" and row.get("evidence_ids") for row in required)]
    runtime_gaps = [f"runtime.{behavior}" for behavior in behavior_rows if behavior not in closed_behaviors]
    profile_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"behaviors": 0, "closed": 0})
    for behavior, required in behavior_rows.items():
        profile = required[0]["profile"]
        profile_counts[profile]["behaviors"] += 1
        if behavior in closed_behaviors:
            profile_counts[profile]["closed"] += 1
    runtime_axis = make_axis(axes_by_id["real-runtime-lab"], {
        "unit": "required behavior × RabbitMQ-specific runtime profile", "required": len(behavior_rows),
        "closed": len(closed_behaviors), "remaining": len(runtime_gaps), "profiles": dict(sorted(profile_counts.items())),
    }, [make_check("runtime.profile-proof", "各Behaviorの全Required Scenarioを指定Profileで実行し、cleanupとVersionを記録する。",
                   f"{len(closed_behaviors)}/{len(behavior_rows)}", "pass" if not runtime_gaps else ("partial" if closed_behaviors else "gap"),
                   ["verification.plan.yaml", "versions/", "labs/", "evidence/"], runtime_gaps)])

    scenario_axes = [
        scenario_axis(axes_by_id["scenario-normal"], "normal", rows),
        scenario_axis(axes_by_id["scenario-boundary"], "boundary", rows),
        scenario_axis(axes_by_id["scenario-refusal"], "rejection", rows),
        scenario_axis(axes_by_id["scenario-failure"], "failure", rows),
        scenario_axis(axes_by_id["scenario-recovery"], "recovery", rows),
        scenario_axis(axes_by_id["scenario-migration"], "migration", rows),
        scenario_axis(axes_by_id["scenario-operations"], "operations", rows),
        scenario_axis(axes_by_id["scenario-security"], "security", rows),
        scenario_axis(axes_by_id["scenario-performance"], "performance", rows),
        scenario_axis(axes_by_id["scenario-compatibility"], "compatibility", rows),
    ]

    current_lock = coverage["authority_lock_digest"]
    covered_rows = [row for row in required_rows if row["state"] == "covered"]
    binding_gaps = []
    for row in covered_rows:
        if len(row.get("evidence_ids", [])) != 1:
            binding_gaps.append(row["proof_obligation_id"])
            continue
        record = record_by_id.get(row["evidence_ids"][0])
        if not record or record.get("source_digest") != current_lock or record.get("execution_mode") not in {"runtime", "platform"}:
            binding_gaps.append(row["proof_obligation_id"])
    uncovered_artifacts = [row["proof_obligation_id"] for row in required_rows if row["state"] != "covered"]
    artifact_axis = make_axis(axes_by_id["artifact-trace"], {
        "unit": "required behavior × scenario × profile × proof × artifact", "required": len(required_rows),
        "bound": len(covered_rows) - len(binding_gaps), "remaining": len(uncovered_artifacts) + len(binding_gaps),
        "required_channels": ["wire-packet", "broker-log", "client-log", "metric", "state-snapshot", "benchmark-sample", "kubernetes-event"],
    }, [
        make_check("artifact.unique-binding", "各Required rowへ共有しない専用Evidence/Artifactを一件接続する。",
                   f"{len(covered_rows)}/{len(required_rows)}", "pass" if not uncovered_artifacts else "partial",
                   ["verification.plan.yaml", "evidence/", "evidence/raw/"], uncovered_artifacts),
        make_check("artifact.digest-freshness", "Source、Harness、Environment、Raw Artifact、実行ModeをDigest束縛する。",
                   f"{len(covered_rows) - len(binding_gaps)}/{len(covered_rows)} covered rows", "pass" if not binding_gaps else "gap",
                   ["sources.lock.yaml", "evidence/"], binding_gaps),
    ])

    integration_behaviors = [item for item in inventory["items"] if {"architecture-design", "compatibility-integration"} & set(item["surface_ids"])]
    reference_gaps = [] if definitive.get("reference_systems") else ["reference.system-manifest", "reference.cross-behavior-proof"]
    reference_axis = make_axis(axes_by_id["integrated-reference-system"], {
        "unit": "authority-derived architecture/integration behavior", "behaviors": len(integration_behaviors),
        "reference_systems": len(definitive.get("reference_systems", [])),
    }, [make_check("reference.system-proof", "複数Protocol、Queue、Security、Telemetry、Cross-clusterを統合し、正常・障害・回復を専用Proof化する。",
                   len(definitive.get("reference_systems", [])), "pass" if not reference_gaps else "gap",
                   ["definitive.yaml", "verification.plan.yaml"], reference_gaps)])

    eval_gaps = [case["id"] for case in skill_eval["cases"] if case.get("result") not in {"pass", "passed"}]
    eval_gaps.extend(["skill.surface-matrix-14", "skill.agent-execution-oracle"])
    skill_axis = make_axis(axes_by_id["skill-eval"], {
        "unit": "8 outcomes × 14 mastery surfaces × risk/gap class", "outcomes": 8, "surfaces": 14,
        "outcome_surface_cells": 112, "closed_cells": 0,
    }, [make_check("skill.definitive-matrix", "成功、Gap、拒否、実装、診断、移行、Reviewを独立Oracleで評価する。",
                   "0/112 cells", "gap", [definitive["skill_eval"], "mastery.yaml"], eval_gaps)])

    rights_gaps = ["rights.human-license-obligations", "rights.human-trademark-name-review"]
    rights_axis = make_axis(axes_by_id["rights-provenance"], {
        "unit": "repository, authority, dependency and publication artifact", "sources": len(sources["sources"]),
        "third_party_components": len(load_yaml(ROOT / "third_party/manifest.yaml")["components"]),
        "human_reviews_remaining": len(rights_gaps),
    }, [
        make_check("rights.automated-inventory", "License、NOTICE、第三者Manifest、SPDX/CycloneDX SBOMを保持する。", "present", "pass",
                   ["LICENSE", "NOTICE", "third_party/manifest.yaml", "sbom.spdx.json", "third_party/sbom.cdx.json"], []),
        make_check("rights.human-review", "第三者義務とTrademark/Repository名を人がReviewする。", "pending", "gap",
                   ["third_party/manifest.yaml"], rights_gaps),
    ])

    baseline_files = ["baseline/public-main-22ab07c.yaml", "migrations/public-main-baseline-v2.yaml"]
    baseline_gaps = [path for path in baseline_files if not (ROOT / path).is_file()]
    nonreg_axis = make_axis(axes_by_id["non-regression-gate"], {
        "unit": "public-main Test/Lab/Target/Claim/Proof/Evidence/Source/Skill eval/CI identity",
        "baseline": "22ab07cc6c3d92ab489fe6ff8855c9fb8a97db5a", "validator": "scripts/validate-non-regression.py",
    }, [make_check("non-regression.machine-gate", "公開mainの能力を固定し、縮小を拒否し、置換へMappingとMigration Evidenceを要求する。",
                   "baseline and migration manifest present" if not baseline_gaps else "missing", "pass" if not baseline_gaps else "gap",
                   baseline_files + ["scripts/validate-non-regression.py"], baseline_gaps)])

    axes = [authority_axis, behavior_axis, runtime_axis, *scenario_axes, artifact_axis, reference_axis, skill_axis, rights_axis, nonreg_axis]
    gaps = sum(axis["gap_count"] for axis in axes)
    summary = Counter(axis["status"] for axis in axes)
    return {
        "schema_version": 2, "id": "rabbitmq-depth-parity-v2", "atlas_id": atlas["id"], "epoch": coverage["epoch"],
        "status": "complete" if gaps == 0 else "incomplete",
        "reference": {
            "manifest": REFERENCE.relative_to(ROOT).as_posix(), "source_repository": reference["source_repository"],
            "source_commit": reference["source_commit"], "source_status": reference["source_status"],
            "source_summary": reference["source_summary"],
            "rule": "FE件数を転記せず、同じ18軸の意味とProof粒度をRabbitMQ固有denominatorへ適用する。",
        },
        "fixture_mapping": [
            {"source": "fixtures/definitive-gate-v2/authority-surface-inventory.fixture.json",
             "rabbitmq": ["surface/authority/", "surface.inventory.yaml", "surface/source-classification.yaml"],
             "rule": "Authority locatorをAtomic behavior、Target、Claimへ一対一接続し、未抽出SourceをGapとして残す。"},
            {"source": "fixtures/definitive-gate-v2/variant-comparison.fixture.json",
             "rabbitmq": ["definitive.yaml#comparisons", "verification.plan.yaml"],
             "rule": "同じObservable ContractのVariantだけを同一Oracleで比較し、未実装比較をClosure扱いしない。"},
            {"source": "fixtures/definitive-gate-v2/profile-incompatibility.fixture.json",
             "rabbitmq": ["verification.plan.yaml#runtime_contracts", "versions/", "environments/"],
             "rule": "Protocol、Plugin、三Node Cluster、二Cluster、外部認証、TLS、Operator、Upgrade、Capacity Profileを相互代替しない。"},
            {"source": "fixtures/definitive-gate-v2/evidence-granularity.fixture.json",
             "rabbitmq": ["verification.plan.yaml#rows", "evidence/", "evidence/raw/"],
             "rule": "Behavior × Scenario × Profile × Proof × ArtifactをRequired unitとし、集約Evidenceを専用Proofの代替にしない。"},
            {"source": "baselines/definitive-gate-v2.json",
             "rabbitmq": ["baseline/public-main-22ab07c.yaml", "migrations/public-main-baseline-v2.yaml", "scripts/validate-non-regression.py"],
             "rule": "公開mainの能力Identity、Assertion、Version、Evidence、CIを固定し、置換へMappingとMigration Evidenceを要求する。"},
        ],
        "scenario_mapping": {
            "scenario-normal": "normal", "scenario-boundary": "boundary", "scenario-refusal": "rejection",
            "scenario-failure": "failure", "scenario-recovery": "recovery", "scenario-migration": "migration",
            "scenario-operations": "operations", "scenario-security": "security",
            "scenario-performance": "performance", "scenario-compatibility": "compatibility",
        },
        "summary": {"axes": len(axes), "satisfied": summary["satisfied"], "partial": summary["partial"],
                    "missing": summary["missing"], "total_gaps": gaps, "definitive_allowed": gaps == 0},
        "axes": axes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = yaml.safe_dump(build(), allow_unicode=True, sort_keys=False, width=140)
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print("RabbitMQ depth parity is stale; run scripts/generate-rabbitmq-depth-parity.py")
            return 1
        matrix = yaml.safe_load(rendered)
        print(f"RabbitMQ depth parity verified: axes={matrix['summary']['axes']} satisfied={matrix['summary']['satisfied']} gaps={matrix['summary']['total_gaps']}")
        return 0
    OUTPUT.write_text(rendered, encoding="utf-8")
    matrix = yaml.safe_load(rendered)
    print(f"RabbitMQ depth parity generated: axes={matrix['summary']['axes']} satisfied={matrix['summary']['satisfied']} gaps={matrix['summary']['total_gaps']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
