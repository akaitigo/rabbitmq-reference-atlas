#!/usr/bin/env python3
"""Generate the Core v2 depth parity manifest without reducing the RabbitMQ denominator."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "rabbitmq-depth-parity.yaml"
OUTPUT = ROOT / "depth.parity.yaml"

REFERENCE = {
    "id": "fe-depth-reference-v1",
    "path": "authority/FE_DEPTH_REFERENCE.json",
    "digest": "sha256:2452696f9807b7d4a8ffb22b3ba37f079a25a34ac2370d78423445b96064582a",
    "repository": "frontend-behavior-atlas",
    "commit": "4a0b2df8e2091a963bd0e0e1bbccef9c84b49a45",
    "status_at_commit": "incomplete",
}


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a mapping")
    return value


def build_manifest(source: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen_axes: set[str] = set()

    for axis in source["axes"]:
        axis_id = axis["id"]
        seen_axes.add(axis_id)
        gap_ids = axis.get("gap_ids", [])
        for gap_id in gap_ids:
            rows.append(
                {
                    "behavior_id": gap_id,
                    "variant_id": f"depth.{axis_id}",
                    "axis": axis_id,
                    "status": "gap",
                    "gap_count": 1,
                    "proof_id": None,
                    "oracle": None,
                    "evidence_ids": [],
                    "artifact_uri": None,
                    "trace_id": None,
                    "rationale": (
                        f"RabbitMQ固有denominatorの未Closure `{gap_id}` を保持する。"
                        "専用Proof、Oracle、Artifact、Traceが原子的に結合されるまでsatisfiedへ昇格しない。"
                    ),
                }
            )

        if not gap_ids:
            # The legacy matrix can call an axis satisfied without the explicit
            # proof/oracle/artifact/trace tuple required by Core v2.  Preserve
            # that stronger denominator as a visible gap instead of inventing a
            # trace identifier or omitting the axis.
            rows.append(
                {
                    "behavior_id": f"depth.{axis_id}",
                    "variant_id": "core-v2.explicit-trace",
                    "axis": axis_id,
                    "status": "gap",
                    "gap_count": 1,
                    "proof_id": None,
                    "oracle": None,
                    "evidence_ids": [],
                    "artifact_uri": None,
                    "trace_id": None,
                    "rationale": (
                        f"{axis_id}の既存Gateは通過しているが、Core v2が要求する専用Proof、Oracle、"
                        "Artifact、Traceの同一row結合が未固定であるため未Closureとして保持する。"
                    ),
                }
            )

    if len(seen_axes) != len(source["axes"]):
        raise ValueError("depth parity axes must be unique")

    return {
        "schema_version": 2,
        "atlas_id": source["atlas_id"],
        "epoch": source["epoch"],
        "completion_status": "incomplete",
        "reference": REFERENCE,
        "denominator_policy": {
            "source": "authority-derived-subject-surface-inventory",
            "transplant_absolute_counts": False,
        },
        "rows": rows,
    }


def render(manifest: dict[str, Any]) -> str:
    return yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=120)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    expected = render(build_manifest(load_yaml(SOURCE)))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            print("Core v2 depth parity is stale; run scripts/generate-core-depth-parity.py")
            return 1
        matrix = yaml.safe_load(expected)
        print(
            "Core v2 depth parity verified: "
            f"rows={len(matrix['rows'])} gaps={sum(row['gap_count'] for row in matrix['rows'])}"
        )
        return 0

    OUTPUT.write_text(expected, encoding="utf-8")
    matrix = yaml.safe_load(expected)
    print(
        "Core v2 depth parity generated: "
        f"rows={len(matrix['rows'])} gaps={sum(row['gap_count'] for row in matrix['rows'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
