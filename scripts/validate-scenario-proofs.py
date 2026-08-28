#!/usr/bin/env python3
from scenario_proof import validate_files

errors = validate_files()
if errors:
    for error in errors:
        print(f"ERROR: {error}")
    raise SystemExit(1)
print("Scenario Proof検証通過: 2,060判定Artifact、legacy observation 12、Scenario gap closed 0、Completion eligible 0")
