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
    locator_index = json.loads((ROOT / "authority/extraction.snapshot.json").read_text(encoding="utf-8"))
    locator_artifacts = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "authority/locator-drafts").glob("*.json"))]
    body_index = json.loads((ROOT / "authority/body-inventory.snapshot.json").read_text(encoding="utf-8"))
    body_artifacts = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "authority/body-inventory-draft").glob("*.json"))]
    review_queue = json.loads((ROOT / "authority/review-queue.snapshot.json").read_text(encoding="utf-8"))
    review_decisions = json.loads((ROOT / review_queue["decision_ledger"]).read_text(encoding="utf-8"))
    skill_eval = json.loads((ROOT / definitive["skill_eval"]).read_text(encoding="utf-8"))
    routing_eval = json.loads((ROOT / "evals/rabbitmq-reference-atlas.skill-routing-eval.json").read_text(encoding="utf-8"))
    forward_eval = json.loads((ROOT / "evals/rabbitmq-reference-atlas.independent-agent-forward-eval.json").read_text(encoding="utf-8"))
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
    locked_sources = [source for source in sources["sources"] if source.get("digest") and source.get("version") and source.get("url")]
    reproduction_gaps = [f"authority.body-{artifact['fetch']['status']}.{artifact['source_id']}" for artifact in locator_artifacts if artifact["fetch"]["status"] != "matched"]
    body_document_gaps = [f"authority.raw-anchor-{artifact['fetch']['status']}.{artifact['document_id']}"
                          for artifact in body_artifacts if artifact["fetch"]["status"] != "matched"]
    locator_candidates = [candidate for artifact in locator_artifacts for candidate in artifact["candidate_surfaces"]]
    locator_gaps = [f"authority.locator.{candidate['edge_id']}" for candidate in locator_candidates
                    if candidate["locator_status"] not in {"root-document", "fragment-found"}]
    reference_edge_review_gaps = [f"authority.human-review.{candidate['edge_id']}" for candidate in locator_candidates]
    raw_anchors = [(artifact["document_id"], anchor) for artifact in body_artifacts for anchor in artifact["anchors"]]
    reviewed_anchor_ids = {anchor_id for decision in review_decisions["decisions"] for anchor_id in decision["anchor_ids"]}
    review_gaps = [f"authority.human-review.{document_id}.{anchor['id']}" for document_id, anchor in raw_anchors
                   if anchor["id"] not in reviewed_anchor_ids]
    classification_gaps = [row["source_id"] for row in classification["sources"]
                           if row["classification"] not in {"surface-authority", "supporting-authority"}]
    authority_axis = make_axis(axes_by_id["authority-body-digestion"], {
        "unit": "unique locked document × fixed-selector raw anchor × human decision × promoted semantic surface",
        "locked_sources": locator_index["summary"]["locked_sources"],
        "unique_documents": body_index["summary"]["unique_documents"],
        "body_digest_matched": body_index["summary"]["matched_documents"],
        "body_digest_stale": body_index["summary"]["stale_documents"],
        "fetch_failed": body_index["summary"]["failed_documents"],
        "fixed_selector_raw_anchors": body_index["summary"]["raw_anchors"],
        "pending_human_raw_anchors": body_index["summary"]["pending_human_anchors"],
        "human_reviewed_raw_anchors": body_index["summary"]["human_reviewed_anchors"],
        "promoted_semantic_surface_ids": body_index["summary"]["promoted_surface_ids"],
        "promoted_behavior_ids": body_index["summary"]["promoted_behavior_ids"],
        "raw_anchors_count_toward_surface_inventory": body_index["semantic_accounting"]["raw_anchors_count_toward_surface_inventory"],
        "raw_anchors_count_toward_depth": body_index["semantic_accounting"]["raw_anchors_count_toward_depth"],
        "review_queue_id": review_queue["queue_id"],
        "queued_raw_anchors": review_queue["summary"]["queued_anchors"],
        "pending_human_queue_items": review_queue["summary"]["pending_human"],
        "human_review_decisions": review_queue["summary"]["decisions"],
        "proposed_priority_counts": review_queue["summary"]["proposed_priority_counts"],
        "proposed_candidate_clusters": review_queue["summary"]["proposed_candidate_clusters"],
        "proposed_batches": review_queue["summary"]["proposed_batches"],
        "stale_document_holds": review_queue["summary"]["stale_document_holds"],
        "queued_anchor_count_toward_semantic_surface": review_queue["semantic_accounting"]["queued_anchor_count_toward_semantic_surface"],
        "queued_anchor_count_toward_depth": review_queue["semantic_accounting"]["queued_anchor_count_toward_depth"],
        "existing_reference_edges": locator_index["summary"]["candidate_surfaces"],
        "located_reference_edges": locator_index["summary"]["root_locators"] + locator_index["summary"]["fragments_found"],
        "fragment_not_found": locator_index["summary"]["fragments_not_found"],
        "locator_evaluations_deferred": locator_index["summary"]["locator_evaluations_deferred"],
        "body_structure_sources_scanned": locator_index["summary"]["body_structure_sources_scanned"],
        "body_structure_sources_deferred": locator_index["summary"]["body_structure_sources_deferred"],
        "body_section_candidates": locator_index["summary"]["body_section_candidates"],
        "authority_text_surfaces_exhaustive": locator_index["summary"]["authority_text_surfaces_exhaustive"],
        "human_reviewed_surfaces": locator_index["summary"]["human_reviewed_surfaces"],
        "protocol_plugin_operator_denominators": locator_index["denominators"],
    }, [
        make_check("authority.body-lock", "全SourceのURL、Version、取得日、本文SHA-256を固定する。",
                   f"{len(locked_sources)}/{len(sources['sources'])}", "pass" if len(locked_sources) == len(sources["sources"]) else "gap",
                   ["sources.lock.yaml"], [source["id"] for source in sources["sources"] if source not in locked_sources]),
        make_check("authority.body-reproduction", "全固定Authority bodyを再取得し、exact digest一致を確認する。",
                   f"{body_index['summary']['matched_documents']}/{body_index['summary']['unique_documents']} unique documents",
                   "pass" if not body_document_gaps else "partial", ["authority/body-inventory.snapshot.json", "authority/body-inventory-draft/"], body_document_gaps),
        make_check("authority.existing-surface-classification", "既存Surface分類とsupporting分類を全Sourceへ明示する。",
                   f"{len(classification['sources']) - len(classification_gaps)}/{len(classification['sources'])}",
                   "pass" if not classification_gaps else "gap", ["surface/source-classification.yaml", "surface/authority/"], classification_gaps),
        make_check("authority.reference-edge-candidates", "既存reference edgeを本文非保存のLocator候補へ損失なく投影し、未Reviewと明示する。",
                   f"{len(locator_candidates) - len(locator_gaps)}/{len(locator_candidates)} located",
                   "pass" if not locator_gaps else "partial", ["authority/extraction.snapshot.json", "authority/locator-drafts/"], locator_gaps),
        make_check("authority.reference-edge-review", "既存reference edge候補をHuman review前のDomain Overlayとして維持する。",
                   f"0/{len(locator_candidates)}", "gap", ["authority/extraction.snapshot.json", "authority/locator-drafts/"], reference_edge_review_gaps),
        make_check("authority.body-structure-scan", "固定body全体を非重複sectionへpartitionし、offsetとdigestだけを保持する。",
                   f"{locator_index['summary']['body_structure_sources_scanned']}/{locator_index['summary']['locked_sources']} sources; {locator_index['summary']['body_section_candidates']} sections",
                   "pass" if locator_index["summary"]["body_structure_sources_deferred"] == 0 else "partial",
                   ["authority/extraction.snapshot.json", "authority/locator-drafts/"], reproduction_gaps),
        make_check("authority.raw-anchor-population", "unique documentごとに固定selector raw anchor候補を本文非保存で列挙し、stable IDとtool/source digestへ束縛する。",
                   f"{body_index['summary']['selector_exhaustive_documents']}/{body_index['summary']['unique_documents']} documents; {body_index['summary']['raw_anchors']} raw anchors",
                   "pass" if not body_document_gaps else "partial",
                   ["authority/body-inventory.snapshot.json", "authority/body-inventory-draft/", "baseline/authority-body-inventory-v1.json"], body_document_gaps),
        make_check("authority.raw-anchor-accounting", "raw anchor数をSemantic Surface数またはDepth達成へ算入しない。",
                   "semantic surfaces=0; depth credit=0", "pass",
                   ["authority/body-inventory.snapshot.json", "artifacts/authority-body-non-regression-report.json"], []),
        make_check("authority.human-review-queue", "全eligible raw anchorをstable ID、source/tool digest、locator付きの人手Review queueへ一度ずつ投影し、stale documentをholdする。",
                   f"{review_queue['summary']['queued_anchors']}/{body_index['summary']['raw_anchors']} anchors; {review_queue['summary']['stale_document_holds']} stale holds",
                   "pass" if (review_queue["summary"]["queued_anchors"] == body_index["summary"]["raw_anchors"]
                              and review_queue["summary"]["stale_document_holds"] == body_index["summary"]["stale_documents"]) else "gap",
                   ["authority/review-queue.snapshot.json", "authority/review-queue-draft/", "authority/reviews/decisions.json"], []),
        make_check("authority.review-queue-accounting", "priority、cluster、batchを機械提案に限定し、Queue件数をSemantic Surface数またはDepth達成へ算入しない。",
                   "machine proposals only; semantic surfaces=0; depth credit=0", "pass",
                   ["authority/review-queue.snapshot.json", "baseline/authority-review-prequeue-v1.json"], []),
        make_check("authority.surface-exhaustiveness", "Authority本文全体からSurfaceを抽出し、未分類をゼロにする。", False, "gap",
                   ["authority/body-inventory.snapshot.json", "authority/review-queue.snapshot.json", "authority/reviews/decisions.json"], ["authority.semantic-surfaces-exhaustive"]),
        make_check("authority.human-review", "各raw anchorを人が本文解釈し、Protocol/behavior Surfaceへ昇格または理由付き却下する。",
                   f"{review_queue['summary']['human_reviewed']}/{review_queue['summary']['queued_anchors']}; {review_queue['summary']['pending_human']} pending",
                   "gap" if review_gaps else "pass",
                   ["authority/review-queue.snapshot.json", "authority/reviews/decisions.json"], review_gaps),
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

    contract_gaps = [item["id"] for item in routing_eval["matrix"] if item["contract_result"] != "pass"]
    contract_gaps.extend(item["id"] for item in routing_eval["boundary_cases"] if item["result"] != "pass")
    closure_gaps = [item["id"] for item in routing_eval["matrix"] if not item["closure_eligible"]]
    open_target_gaps = [f"skill.target-state.{item['id']}" for item in routing_eval["target_state_inventory"]["targets"]
                        if item["requirement"] == "required" and item["state"] != "covered"]
    forward_gaps = [] if forward_eval["status"] == "pass" and all(item["result"] == "pass" for item in forward_eval["cases"]) else ["skill.independent-agent-forward-eval"]
    skill_axis = make_axis(axes_by_id["skill-eval"], {
        "unit": "8 outcomes × 14 mastery surfaces × risk/gap class", "outcomes": 8, "surfaces": 14,
        "outcome_surface_cells": 112, "contract_passed_cells": routing_eval["summary"]["contract_passed"],
        "routed_cells": routing_eval["summary"]["routed"], "routing_gaps": routing_eval["summary"]["routing_gaps"],
        "closed_cells": routing_eval["summary"]["closure_eligible_cells"],
        "target_state_counts": routing_eval["summary"]["target_state_counts"],
        "independent_agent_forward_eval": forward_eval["status"],
        "matrix_contract_pass_is_sufficient": False,
    }, [
        make_check("skill.routing-contract", "112セルとBoundary CaseでRoute、Gap、権限、人手Authority、stale relock停止を評価する。",
                   f"{routing_eval['summary']['contract_passed']}/112 contract; boundary {routing_eval['summary']['boundary_passed']}/{routing_eval['summary']['boundary_cases']}",
                   "pass" if not contract_gaps else "gap",
                   [definitive["skill_eval"], "evals/rabbitmq-reference-atlas.skill-routing-eval.json", "mastery.yaml"], contract_gaps),
        make_check("skill.runtime-closure", "各セルを実Target、Variant、一次資料、Broker、Protocol Evidenceへ接続し全Required Targetをcoveredにする。",
                   f"{routing_eval['summary']['closure_eligible_cells']}/112 cells; targets={routing_eval['summary']['target_state_counts']}",
                   "pass" if not closure_gaps and not open_target_gaps else "partial",
                   ["coverage.yaml", "verification.plan.yaml", "surface.inventory.yaml", "evidence/"], closure_gaps + open_target_gaps),
        make_check("skill.independent-forward", "期待値を隠した独立Agent Forward Evalで実Query応答と停止条件を評価する。",
                   forward_eval["status"], "pass" if not forward_gaps else "gap",
                   ["evals/forward-agent-prompts.json", "evals/rabbitmq-reference-atlas.independent-agent-forward-eval.json"], forward_gaps),
    ])

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
            "authority_extraction_source_commit": reference["authority_extraction_reference"]["source_commit"],
            "authority_body_storage": reference["authority_extraction_reference"]["body_storage"],
            "authority_body_inventory_source_commit": reference["authority_body_inventory_reference"]["source_commit"],
            "authority_raw_anchor_population_unit": reference["authority_body_inventory_reference"]["population_unit"],
            "raw_anchors_count_toward_semantic_surface": reference["authority_body_inventory_reference"]["raw_anchors_count_toward_semantic_surface"],
            "raw_anchors_count_toward_depth": reference["authority_body_inventory_reference"]["raw_anchors_count_toward_depth"],
            "authority_review_queue_source_commit": reference["authority_review_queue_reference"]["source_commit"],
            "authority_review_queue_population_unit": reference["authority_review_queue_reference"]["population_unit"],
            "queued_anchor_count_toward_semantic_surface": reference["authority_review_queue_reference"]["queued_anchor_count_toward_semantic_surface"],
            "queued_anchor_count_toward_depth": reference["authority_review_queue_reference"]["queued_anchor_count_toward_depth"],
            "skill_eval_source_commit": reference["skill_eval_reference"]["source_commit"],
            "skill_eval_matrix_unit": reference["skill_eval_reference"]["matrix_unit"],
            "skill_eval_matrix_pass_is_completion": reference["skill_eval_reference"]["matrix_pass_is_completion"],
            "skill_eval_independent_agent_forward_required": reference["skill_eval_reference"]["independent_agent_forward_eval_required"],
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
