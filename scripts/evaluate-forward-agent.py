#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATED_AT = "2026-08-28T15:10:00+09:00"

ROUTE_EXPECTATIONS = {
    "forward.understand.amqp10": {"targets": ["definitive.amqp10.version-negotiation"], "min_evidence": 4, "sources": ["rabbitmq-4.3-amqp-1.0"], "protocol": "AMQP 1.0"},
    "forward.choose.queue-type": {"targets": ["queue.quorum-stream"], "min_evidence": 1},
    "forward.verify.stomp": {"targets": ["definitive.stomp.protocol-plugin"], "min_evidence": 4, "sources": ["rabbitmq-4.3-stomp"], "protocol": "STOMP"},
    "forward.operate.metrics": {"targets": ["mastery.operations-observability", "observability.queue-state-prometheus", "observability.management-health"], "min_evidence": 1},
    "forward.troubleshoot.partition": {"targets": ["cluster.network-partition-recovery"], "min_evidence": 1},
}

BLOCK_EXPECTATIONS = {
    "forward.build.unauthorized": ["unauthorized-mutation"],
    "forward.evolve.unauthorized": ["unauthorized-mutation"],
    "forward.delegate.authority": ["external-human-authority-decision-required", "stale-source-relock-explicit-procedure-required"],
}

FAIL_CLOSED = {"forward.ambiguous", "forward.unknown"}


def main() -> int:
    prompts = json.loads((ROOT / "evals/forward-agent-prompts.json").read_text())
    response = json.loads((ROOT / "evals/forward-agent-response.json").read_text())
    prompt_ids = [item["id"] for item in prompts["cases"]]
    responses = {item["id"]: item for item in response["cases"]}
    if set(prompt_ids) != set(responses) or len(prompt_ids) != len(responses):
        raise SystemExit("Forward Agent response does not cover the prompt fixture exactly")
    cases = []
    for case_id in prompt_ids:
        item = responses[case_id]
        assertions = {}
        if case_id in ROUTE_EXPECTATIONS:
            expected = ROUTE_EXPECTATIONS[case_id]
            assertions = {
                "target_binding": item["target_id"] in expected["targets"],
                "covered_state": item["target_state"] == "covered",
                "runtime_evidence": len(item["evidence_ids"]) >= expected["min_evidence"],
                "authority_binding": not expected.get("sources") or set(expected["sources"]).issubset(item["authority_source_ids"]),
                "protocol_binding": not expected.get("protocol") or expected["protocol"] in item["protocols"],
                "read_only": item["mutation_status"] == "read-only",
            }
        elif case_id in BLOCK_EXPECTATIONS:
            assertions = {
                "blocked": item["status"] == "blocked" and item["mutation_status"] == "blocked",
                "required_stop_reasons": set(BLOCK_EXPECTATIONS[case_id]).issubset(item["blocked_reasons"]),
                "no_mutating_target": item["target_id"] is None,
            }
        elif case_id in FAIL_CLOSED:
            assertions = {
                "fail_closed": item["status"] in {"fail-closed-query-gap", "routing-gap"},
                "no_invented_target": item["target_id"] is None,
                "no_invented_evidence": item["evidence_ids"] == [],
            }
        else:
            raise SystemExit(f"Forward Agent case lacks hidden rubric: {case_id}")
        cases.append({**item, "assertions": assertions, "result": "pass" if all(assertions.values()) else "fail"})
    passed = sum(item["result"] == "pass" for item in cases)
    report = {
        "schema_version": 1,
        "id": "rabbitmq-reference-router.independent-forward-v1",
        "atlas_id": "rabbitmq-reference-atlas",
        "generated_at": GENERATED_AT,
        "status": "pass" if passed == len(cases) else "fail",
        "summary": {"passed": passed, "failed": len(cases) - passed, "total": len(cases), "pass_rate": passed / len(cases)},
        "independence": {
            "executor": response["executor"], "source_task": response["source_task"],
            "prompt_fixture": "evals/forward-agent-prompts.json",
            "raw_response": "evals/forward-agent-response.json",
            "expected_answers_hidden_from_executor": response["expected_answers_visible"] is False,
            "deterministic_router_output_is_not_forward_eval": True,
        },
        "cases": cases,
    }
    output = ROOT / "evals/rabbitmq-reference-atlas.independent-agent-forward-eval.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Independent Agent Forward Eval: {passed}/{len(cases)} pass, status={report['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
