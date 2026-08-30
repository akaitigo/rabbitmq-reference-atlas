#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys

from skill_routing import (
    OUTCOME_EXECUTION,
    ROOT,
    RoutingContext,
    candidates_for,
    evaluate_matrix_plan,
    matrix_requests,
    plan_request,
    sha_file,
    target_state_inventory,
)


GENERATED_AT = "2026-08-28T15:00:00+09:00"


def boundary_cases(context: RoutingContext) -> list[dict]:
    ambiguous_candidates = candidates_for(context, "choose", "decision-comparison")
    ambiguous_query = " または ".join(item["title"] for item in ambiguous_candidates[:2])
    requests = [
        {"id": "boundary.ambiguous", "outcome": "choose", "surface": "decision-comparison", "query": ambiguous_query},
        {"id": "boundary.unknown", "outcome": "understand", "surface": "orientation-scope", "query": "quantum hologram telepathy interface"},
        {"id": "boundary.unauthorized-build", "outcome": "build", "surface": "implementation-construction", "query": "RabbitMQ virtual host configure write and read permissions"},
        {"id": "boundary.human-authority", "outcome": "delegate", "surface": "agent-skill", "query": "RabbitMQ Skill評価を人手一次資料判断へ昇格する", "authorized_change": True, "authority_semantic_decision": True},
        {"id": "boundary.stale-relock", "outcome": "evolve", "surface": "provenance-rights", "query": "RabbitMQ source lockを更新する", "authorized_change": True, "stale_source_relock": True},
    ]
    expected = {
        "boundary.ambiguous": ("routing-gap", "ambiguous-or-unknown"),
        "boundary.unknown": ("routing-gap", "ambiguous-or-unknown"),
        "boundary.unauthorized-build": ("blocked", "unauthorized-mutation"),
        "boundary.human-authority": ("blocked", "external-human-authority-decision-required"),
        "boundary.stale-relock": ("blocked", "stale-source-relock-explicit-procedure-required"),
    }
    results = []
    for request in requests:
        plan = plan_request(context, request)
        expected_status, expected_reason = expected[request["id"]]
        if expected_reason == "ambiguous-or-unknown":
            passed = plan["status"] == expected_status and plan["target_binding"] is None and plan["routing_gap"] in {"ambiguous-query", "unknown-query"}
        else:
            passed = plan["status"] == expected_status and expected_reason in plan["blocked_reasons"]
        results.append({**plan, "expected_status": expected_status, "expected_reason": expected_reason, "result": "pass" if passed else "fail"})
    return results


def main() -> int:
    context = RoutingContext(ROOT)
    matrix = []
    for request, expected_target in matrix_requests(context):
        plan = plan_request(context, request)
        assertions = evaluate_matrix_plan(plan, expected_target)
        matrix.append({
            **plan, "expected_target_id": expected_target,
            "contract_result": "pass" if all(assertions.values()) else "fail", "assertions": assertions,
        })
    boundaries = boundary_cases(context)
    target_states = target_state_inventory(context)
    forward_path = ROOT / "evals/rabbitmq-reference-atlas.independent-agent-forward-eval.json"
    forward = json.loads(forward_path.read_text())
    forward_pass = forward["status"] == "pass" and all(case["result"] == "pass" for case in forward["cases"])
    routing_gaps = [item["id"] for item in matrix if item["routing_gap"]]
    closure_gaps = [item["id"] for item in matrix if not item["closure_eligible"]]
    contract_failures = [item["id"] for item in matrix if item["contract_result"] != "pass"]
    boundary_failures = [item["id"] for item in boundaries if item["result"] != "pass"]
    completion_ready = not (routing_gaps or closure_gaps or contract_failures or boundary_failures) and target_states["all_required_covered"] and forward_pass
    source_paths = [
        "mastery.yaml", "coverage.yaml", "surface.inventory.yaml", "verification.plan.yaml", "sources.lock.yaml",
        "authority/review-queue.snapshot.json", ".agents/skills/rabbitmq-reference-router/SKILL.md",
        ".agents/skills/rabbitmq-reference-router/references/mastery-contract.json",
        ".agents/skills/rabbitmq-reference-router/scripts/route.py", "scripts/skill_routing.py",
        "scripts/run-definitive-skill-eval.py", "scripts/evaluate-forward-agent.py",
        "evals/forward-agent-prompts.json", "evals/forward-agent-response.json",
        "evals/rabbitmq-reference-atlas.independent-agent-forward-eval.json",
    ]
    artifact = {
        "schema_version": 1,
        "id": "rabbitmq-reference-router.definitive-mastery-v1",
        "atlas_id": "rabbitmq-reference-atlas",
        "generated_at": GENERATED_AT,
        "status": "evaluated-not-completion-certificate" if completion_ready else "incomplete",
        "semantic_scope": "deterministic-routing-contract-plus-independent-agent-forward-eval",
        "reference": {
            "repository": "frontend-behavior-atlas", "commit": "8a9e34a89a55cc53702032783c06ede7246a286f",
            "rule": "FEのOutcome×Surface、Boundary、binding分離方式をRabbitMQ固有Target/Variant/Authority/Broker/Protocol Evidenceへ適用する。",
        },
        "source_bindings": {path: {"path": path, "digest": sha_file(ROOT / path), "bytes": (ROOT / path).stat().st_size} for path in source_paths},
        "summary": {
            "outcomes": len(context.outcomes), "surfaces": len(context.surfaces), "matrix_cells": len(matrix),
            "contract_passed": len(matrix) - len(contract_failures), "contract_failed": len(contract_failures),
            "routed": len(matrix) - len(routing_gaps), "routing_gaps": len(routing_gaps),
            "closure_eligible_cells": len(matrix) - len(closure_gaps), "closure_gap_cells": len(closure_gaps),
            "boundary_cases": len(boundaries), "boundary_passed": len(boundaries) - len(boundary_failures),
            "targets_total": target_states["total"], "target_state_counts": target_states["state_counts"],
            "all_required_targets_covered": target_states["all_required_covered"],
            "independent_agent_forward_eval": forward["status"], "completion_ready": completion_ready,
        },
        "completion_requirements": {
            "matrix_contract_pass_is_sufficient": False,
            "requires_all_112_cells_closure_eligible": True,
            "requires_all_required_targets_covered": True,
            "requires_independent_agent_forward_eval_pass": True,
            "requires_no_routing_source_evidence_or_stale_gap": True,
        },
        "completion_limits": [
            "Router契約matrixのpassだけではTarget、Variant、Authority、実Broker/Protocol EvidenceのClosureを意味しない。",
            "Mastery target_setが交差しないセルはrouting gapであり、暗黙の代替Targetへ接続しない。",
            "planned Target、未接続Evidence、aggregate authority lockだけのBindingはClosureへ算入しない。",
            "人手Authority判断とstale source relockをAgentが代行しない。",
        ],
        "matrix": matrix,
        "boundary_cases": boundaries,
        "target_state_inventory": target_states,
        "independent_agent_forward_eval": {"path": forward_path.relative_to(ROOT).as_posix(), "digest": sha_file(forward_path), "status": forward["status"]},
    }
    detailed = ROOT / "evals/rabbitmq-reference-atlas.skill-routing-eval.json"
    detailed.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")

    core_path = ROOT / "evals/rabbitmq-reference-atlas.definitive-skill-eval.json"
    old = json.loads(core_path.read_text())
    legacy = [case for case in old["cases"] if case["id"].startswith("definitive.")]
    core_cases = legacy + [{
        "id": f"matrix.{item['outcome']}.{item['surface']}",
        "result": "pass" if item["closure_eligible"] else "inconclusive",
        "outcome_ids": [item["outcome"]], "surface_ids": [item["surface"]],
        "gap_behavior": bool(item["routing_gap"] or item.get("closure_gaps")),
        "authorization_boundary": OUTCOME_EXECUTION[item["outcome"]]["mutation_policy"] == "explicit-authorization-required",
        "assertion": f"{item['outcome']}×{item['surface']}をRabbitMQ Target、Variant、Authority、Broker、Protocol EvidenceへRouteする。",
    } for item in matrix]
    core_cases.extend({
        "id": f"boundary.{index + 1}", "result": item["result"],
        "outcome_ids": [item["outcome"]], "surface_ids": [item["surface"]],
        "gap_behavior": True, "authorization_boundary": bool(item["blocked_reasons"]),
        "assertion": f"{item['id']}をfail-closedで判定し、必要な停止条件を保持する。",
    } for index, item in enumerate(boundaries))
    core = {
        "schema_version": 2, "id": "rabbitmq.definitive.router", "atlas_id": "rabbitmq-reference-atlas",
        "atlas_release": "v0.2.0", "skill_id": "rabbitmq-reference-router", "generated_at": GENERATED_AT,
        "cases": core_cases,
    }
    core_path.write_text(json.dumps(core, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Definitive Skill Eval: contract={len(matrix)-len(contract_failures)}/{len(matrix)} "
        f"routed={len(matrix)-len(routing_gaps)} gaps={len(routing_gaps)} "
        f"closure={len(matrix)-len(closure_gaps)}/{len(matrix)} targets={target_states['state_counts']} "
        f"forward={forward['status']} completion_ready={completion_ready}"
    )
    return 1 if contract_failures or boundary_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
