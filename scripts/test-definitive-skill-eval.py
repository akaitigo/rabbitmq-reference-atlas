#!/usr/bin/env python3
import json

from skill_routing import ROOT, RoutingContext, matrix_requests, plan_request


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    context = RoutingContext(ROOT)
    requests = matrix_requests(context)
    require(len(requests) == 112, "8 Outcome×14 Surface matrix must contain 112 cells")
    require(len({request["id"] for request, _ in requests}) == 112, "matrix IDs must be unique")

    unknown = plan_request(context, {"id": "test.unknown", "outcome": "understand", "surface": "orientation-scope", "query": "quantum hologram telepathy"})
    require(unknown["status"] == "routing-gap" and unknown["target_binding"] is None, "unknown query must fail closed")
    unauthorized = plan_request(context, {"id": "test.unauthorized", "outcome": "build", "surface": "implementation-construction", "query": "virtual host configure write and read permissions"})
    require(unauthorized["status"] == "blocked" and "unauthorized-mutation" in unauthorized["blocked_reasons"], "build mutation must require authorization")
    human = plan_request(context, {"id": "test.human", "outcome": "delegate", "surface": "agent-skill", "query": "Agent Skill評価", "authorized_change": True, "authority_semantic_decision": True})
    require(human["status"] == "blocked" and "external-human-authority-decision-required" in human["blocked_reasons"], "human Authority decision must stop")
    stale = plan_request(context, {"id": "test.stale", "outcome": "evolve", "surface": "provenance-rights", "query": "source lock", "authorized_change": True, "stale_source_relock": True})
    require(stale["status"] == "blocked" and "stale-source-relock-explicit-procedure-required" in stale["blocked_reasons"], "stale relock must stop")

    detailed = json.loads((ROOT / "evals/rabbitmq-reference-atlas.skill-routing-eval.json").read_text())
    require(detailed["summary"]["matrix_cells"] == 112, "detailed matrix size mismatch")
    require(detailed["summary"]["targets_total"] == len(context.targets), "all Target states must be recorded")
    require(len(detailed["target_state_inventory"]["targets"]) == len(context.targets), "Target inventory incomplete")
    require(detailed["completion_requirements"]["matrix_contract_pass_is_sufficient"] is False, "matrix pass cannot be completion")
    require(detailed["status"] == "incomplete" and detailed["summary"]["completion_ready"] is False, "open targets must keep eval incomplete")
    require(detailed["summary"]["target_state_counts"].get("planned", 0) > 0, "fixture expects open Targets")
    print(
        "definitive-skill-eval tests PASS: "
        f"matrix={detailed['summary']['matrix_cells']} routed={detailed['summary']['routed']} "
        f"routing_gaps={detailed['summary']['routing_gaps']} closure={detailed['summary']['closure_eligible_cells']} "
        f"targets={detailed['summary']['target_state_counts']} forward={detailed['summary']['independent_agent_forward_eval']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
