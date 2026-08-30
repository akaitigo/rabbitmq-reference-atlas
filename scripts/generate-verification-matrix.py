#!/usr/bin/env python3
"""Behavior×Scenario Proof正本からDefinitive v2 root matrixを生成する。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "evidence/scenarios/index.json"
OUTPUT = ROOT / "verification.matrix.yaml"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def build() -> dict:
    index = load_json(INDEX)
    rows = []
    for item in index["files"]:
        proof = load_json(ROOT / item["path"])
        if proof["id"] != item["id"] or proof["behavior_id"] != item["behavior_id"]:
            raise ValueError(f"scenario index/proof identity mismatch: {item['path']}")
        if proof["scenario"] != item["scenario"] or proof["status"] != item["status"]:
            raise ValueError(f"scenario index/proof state mismatch: {item['path']}")
        if proof["applicability"] == "required":
            closed = proof["closure"]["scenario_gap_closed"] is True
            state = "専用実Broker/Client ProofでScenario Gapを閉じている" if closed else "専用実Broker/Client Proofが未ClosureでGapを保持している"
            rationale = f"{proof['id']}は{state}。Authority atomic bindingを含む全Gate成立まではcompletionへ算入しない。"
            rows.append({
                "behavior_id": proof["behavior_id"],
                "scenario": proof["scenario"],
                "applicability": "required",
                "rationale": rationale,
                "proof_obligation_id": proof["proof_obligation_id"],
                "evidence_ids": [proof["id"]],
                "execution_requirement": "runtime",
                "profile": "cluster",
            })
        elif proof["applicability"] == "not-applicable":
            rows.append({
                "behavior_id": proof["behavior_id"],
                "scenario": proof["scenario"],
                "applicability": "not-applicable",
                "rationale": "Authority由来Surface契約がこのScenarioを非適用と分類しており、実行ProofやCompletion creditを主張しない。",
                "proof_obligation_id": None,
                "evidence_ids": [],
                "execution_requirement": "not-applicable",
                "profile": None,
            })
        else:
            raise ValueError(f"unknown applicability: {item['path']}")
    generated_at = index["generated_at"]
    return {
        "schema_version": 2,
        "atlas_id": "rabbitmq-reference-atlas",
        "epoch": generated_at[:10],
        "rows": rows,
    }


def render(document: dict) -> str:
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=120)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render(build())
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("verification.matrix.yaml is stale; run scripts/generate-verification-matrix.py")
    else:
        OUTPUT.write_text(expected, encoding="utf-8")
    document = yaml.safe_load(expected)
    required = sum(row["applicability"] == "required" for row in document["rows"])
    print(f"verification matrix PASS: rows={len(document['rows'])} required={required}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
