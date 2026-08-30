#!/usr/bin/env python3
"""Queue導入前のAuthority Surface/Behavior IDをpromotion floorとして一度だけ固定する。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "baseline/authority-review-prequeue-v1.json"


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"{OUTPUT.relative_to(ROOT)}は既に存在します。promotion floorを再生成できません")
    authority_surface_ids = []
    for path in sorted((ROOT / "surface/authority").glob("*.authority-surfaces.yaml")):
        authority_surface_ids.extend(item["id"] for item in yaml.safe_load(path.read_text(encoding="utf-8"))["surfaces"])
    inventory = yaml.safe_load((ROOT / "surface.inventory.yaml").read_text(encoding="utf-8"))
    baseline = {
        "schema_version": 1,
        "id": "authority-review-prequeue-v1-2026-08-28",
        "captured_commit": "a40cc2a37b6fe69ba9475b3a19d398ff23b6c066",
        "authority_surface_ids": sorted(authority_surface_ids),
        "atomic_behavior_ids": sorted(item["behavior_id"] for item in inventory["items"]),
    }
    OUTPUT.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"initialized Authority review promotion floor: surfaces={len(authority_surface_ids)} behaviors={len(baseline['atomic_behavior_ids'])}")


if __name__ == "__main__":
    main()
