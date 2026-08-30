#!/usr/bin/env python3
import json

from scenario_proof import evidence_path, validate_files

errors = validate_files()
if errors:
    for error in errors:
        print(f"ERROR: {error}")
    raise SystemExit(1)
summary = json.loads(evidence_path("evidence/scenarios/index.json").read_text(encoding="utf-8"))["summary"]
print(
    f"Scenario Proof検証通過: {summary['rows']:,}判定Artifact、"
    f"legacy observation {summary['legacy_runtime_observation_rows']}、"
    f"Scenario gap closed {summary['scenario_gap_closed_rows']}、"
    f"Completion eligible {summary['completion_eligible_rows']}"
)
