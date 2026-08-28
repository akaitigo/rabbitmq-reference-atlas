#!/usr/bin/env python3
import datetime
import hashlib
import json
import pathlib

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


def digest(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    atlas = yaml.safe_load((ROOT / "atlas.yaml").read_text())
    coverage = yaml.safe_load((ROOT / "coverage.yaml").read_text())
    open_targets = [item["id"] for item in coverage["targets"] if item["requirement"] == "required" and item["state"] != "covered"]
    if open_targets:
        raise SystemExit("Completion Certificateを生成できません。未Closure: " + ", ".join(open_targets))
    if atlas["status"] != "complete":
        raise SystemExit("atlas.yaml statusをcompleteへ変更し、全Gateを再実行してから生成してください")
    records = []
    for path in sorted((ROOT / "evidence").glob("*.evidence.json")):
        record = json.loads(path.read_text())
        if record.get("verdict") != "pass":
            raise SystemExit(f"non-pass Evidence: {record.get('id')}")
        records.append({"id": record["id"], "record_digest": digest(path), "artifact_digest": record["artifact"]["digest"]})
    certificate = {
        "schema_version": 1,
        "atlas_id": atlas["id"],
        "coverage_epoch": atlas["coverage"]["epoch"],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "verdict": "pass",
        "scope": "local verified closure; no GitHub publication or signed release is asserted",
        "manifests": {
            name: digest(ROOT / name)
            for name in ("atlas.yaml", "mastery.yaml", "coverage.yaml", "sources.lock.yaml", "skill.package.yaml", "versions/baseline.yaml")
        },
        "evidence": records,
        "signature": None,
    }
    output = ROOT / atlas["completion"]["certificate"]
    output.write_text(json.dumps(certificate, ensure_ascii=False, indent=2) + "\n")
    print(f"generated {output.relative_to(ROOT)} with {len(records)} Evidence records")


if __name__ == "__main__":
    main()
