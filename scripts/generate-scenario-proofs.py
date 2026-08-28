#!/usr/bin/env python3
from scenario_proof import generate

index = generate()
summary = index["summary"]
print(
    f"Scenario Proof生成: rows={summary['rows']} required={summary['required_rows']} "
    f"behavior_runtime={summary['behavior_specific_runtime_rows']} eligible={summary['completion_eligible_rows']} "
    f"integrated={summary['integrated_passed']}/10"
)
