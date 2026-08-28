#!/usr/bin/env python3
from scenario_proof import generate

index = generate()
summary = index["summary"]
print(
    f"Scenario Proof生成: rows={summary['rows']} required={summary['required_rows']} "
    f"legacy_observation={summary['legacy_runtime_observation_rows']} "
    f"dedicated_reports={summary['dedicated_runtime_report_rows']} "
    f"scenario_closed={summary['scenario_gap_closed_rows']} eligible={summary['completion_eligible_rows']} "
    f"integrated={summary['integrated_passed']}/10"
)
