#!/usr/bin/env python3
"""Non-regression checks for the Core v2 depth parity projection."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "generate-core-depth-parity.py"
SPEC = importlib.util.spec_from_file_location("generate_core_depth_parity", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def reject(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


source = yaml.safe_load((ROOT / "rabbitmq-depth-parity.yaml").read_text(encoding="utf-8"))
actual = yaml.safe_load((ROOT / "depth.parity.yaml").read_text(encoding="utf-8"))
expected = MODULE.build_manifest(source)
reject(actual == expected, "root depth parity must be a deterministic projection")

axes = {axis["id"] for axis in source["axes"]}
row_axes = {row["axis"] for row in actual["rows"]}
reject(row_axes == axes, "all 18 RabbitMQ parity axes must remain represented")

legacy_gap_rows = sum(len(axis.get("gap_ids", [])) for axis in source["axes"])
explicit_core_gaps = sum(1 for axis in source["axes"] if not axis.get("gap_ids", []))
reject(len(actual["rows"]) == legacy_gap_rows + explicit_core_gaps, "gap denominator was reduced or aggregated")
reject(all(row["status"] == "gap" and row["gap_count"] == 1 for row in actual["rows"]), "unproved row was promoted")
reject(all(not row["evidence_ids"] and row["proof_id"] is None for row in actual["rows"]), "gap row reused unrelated proof")
reject(actual["completion_status"] == "incomplete", "depth parity must remain incomplete")
reject(actual["denominator_policy"]["transplant_absolute_counts"] is False, "FE counts must not be transplanted")

# Negative fixtures: deletion, status promotion, and denominator substitution
# must all differ from the deterministic authority-derived projection.
deleted = {**actual, "rows": actual["rows"][:-1]}
reject(deleted != expected, "row deletion mutation was not detected")
promoted = yaml.safe_load(yaml.safe_dump(actual))
promoted["rows"][0]["status"] = "satisfied"
promoted["rows"][0]["gap_count"] = 0
reject(promoted != expected, "unproved satisfied mutation was not detected")
transplanted = yaml.safe_load(yaml.safe_dump(actual))
transplanted["denominator_policy"]["transplant_absolute_counts"] = True
reject(transplanted != expected, "FE absolute-count transplant mutation was not detected")

print(
    "Core v2 depth parity contract passed: "
    f"axes={len(axes)} rows={len(actual['rows'])} gaps={sum(row['gap_count'] for row in actual['rows'])}"
)
