#!/usr/bin/env python3
"""root matrixがScenario denominatorを縮小・集約しないことを固定する。"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_verification_matrix", ROOT / "scripts/generate-verification-matrix.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("verification matrix generatorをloadできません")
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


class MatrixError(ValueError):
    pass


def validate(matrix: dict) -> None:
    expected = generator.build()
    if matrix.get("schema_version") != 2 or matrix.get("atlas_id") != "rabbitmq-reference-atlas":
        raise MatrixError("matrix-identity-changed")
    rows = matrix.get("rows")
    if not isinstance(rows, list) or len(rows) != len(expected["rows"]):
        raise MatrixError("matrix-denominator-shrunk")
    expected_by_key = {(row["behavior_id"], row["scenario"]): row for row in expected["rows"]}
    actual_by_key = {(row.get("behavior_id"), row.get("scenario")): row for row in rows}
    if set(actual_by_key) != set(expected_by_key):
        raise MatrixError("matrix-row-set-changed")
    for key, expected_row in expected_by_key.items():
        row = actual_by_key[key]
        if row["applicability"] != expected_row["applicability"]:
            raise MatrixError("matrix-applicability-changed")
        if row["applicability"] == "required":
            if row["proof_obligation_id"] != expected_row["proof_obligation_id"]:
                raise MatrixError("matrix-proof-obligation-changed")
            if row["evidence_ids"] != expected_row["evidence_ids"]:
                raise MatrixError("matrix-aggregate-evidence-substitution")
            if row["execution_requirement"] != "runtime" or row["profile"] != "cluster":
                raise MatrixError("matrix-runtime-requirement-weakened")
        elif row["proof_obligation_id"] is not None or row["evidence_ids"]:
            raise MatrixError("matrix-not-applicable-credit-added")


matrix = yaml.safe_load((ROOT / "verification.matrix.yaml").read_text(encoding="utf-8"))
validate(matrix)
index = json.loads((ROOT / "evidence/scenarios/index.json").read_text(encoding="utf-8"))
required_rows = [row for row in matrix["rows"] if row["applicability"] == "required"]
assert len(matrix["rows"]) == index["summary"]["rows"] == 2060
assert len(required_rows) == index["summary"]["required_rows"] == 951
assert sum("未Closure" in row["rationale"] for row in required_rows) == index["summary"]["scenario_gap_open_rows"] == 922

mutations = []
removed = copy.deepcopy(matrix)
removed["rows"].pop()
mutations.append((removed, "matrix-denominator-shrunk"))
applicability = copy.deepcopy(matrix)
required_index = next(index for index, row in enumerate(applicability["rows"]) if row["applicability"] == "required")
applicability["rows"][required_index]["applicability"] = "not-applicable"
mutations.append((applicability, "matrix-applicability-changed"))
aggregate = copy.deepcopy(matrix)
aggregate["rows"][required_index]["evidence_ids"] = ["evidence.scenarios.index"]
mutations.append((aggregate, "matrix-aggregate-evidence-substitution"))

for mutated, expected_error in mutations:
    try:
        validate(mutated)
    except MatrixError as error:
        assert str(error) == expected_error, (error, expected_error)
    else:
        raise MatrixError(f"negative mutation accepted: {expected_error}")

print("verification matrix negative contract PASS: 2060 rows/951 required、Gap・専用Proof binding・非集約を固定")
