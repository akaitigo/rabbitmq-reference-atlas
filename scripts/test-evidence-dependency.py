#!/usr/bin/env python3
"""Evidence Dependency Graphの変更・漏れ・退避・構造縮小を拒否する。"""

from __future__ import annotations

import copy
import datetime as dt
import json
import shutil
import tempfile
from pathlib import Path

import yaml

import evidence_dependency_graph as dependency


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/evidence-dependency"


def copy_graph_tree(graph: dict, destination: Path) -> None:
    paths = {member for item in graph["inputs"] for member in item["members"]}
    paths.update(graph["required_outputs"])
    paths.update(item["path"] for item in graph["structures"])
    for relative in sorted(paths):
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def mutate(graph: dict, fixture: dict, root: Path) -> None:
    if fixture["mutation"] == "change-input-and-rebind-digest-without-rerun":
        input_item = next(item for item in graph["inputs"] if item["id"] == fixture["input_id"])
        member = root / fixture["member"]
        member.write_text(member.read_text(encoding="utf-8") + "\n# dependency negative fixture\n", encoding="utf-8")
        current = dependency.aggregate_digest(input_item["members"], root, root / "evidence")
        input_item["current_digest"] = current
        input_item["observed_at"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        for run in graph["runs"]:
            for binding in run["input_bindings"]:
                if binding["input_id"] == input_item["id"]:
                    binding["digest"] = current
    elif fixture["mutation"] == "remove-output-from-run-output-ids":
        output = next(item for item in graph["outputs"] if item["kind"] == fixture["output_kind"])
        run = next(item for item in graph["runs"] if item["id"] == output["run_id"])
        run["output_ids"].remove(output["id"])
    elif fixture["mutation"] == "remove-output-and-required-output-from-graph":
        output = next(item for item in graph["outputs"] if item["kind"] == fixture["output_kind"])
        graph["outputs"] = [item for item in graph["outputs"] if item["id"] != output["id"]]
        graph["required_outputs"].remove(output["path"])
        run = next(item for item in graph["runs"] if item["id"] == output["run_id"])
        run["output_ids"].remove(output["id"])
    elif fixture["mutation"] == "remove-closure-row-and-repin-graph-structure":
        plan_path = root / "evidence/scenarios/closure-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        removed = plan["rows"].pop()
        for tranche in plan["tranches"]:
            if removed["id"] in tranche["row_ids"]:
                tranche["row_ids"].remove(removed["id"])
                tranche["pattern_rows"] -= 1
                tranche["variant_runs"] -= len(removed["variant_ids"])
        plan["tranches"] = [item for item in plan["tranches"] if item["row_ids"]]
        plan["summary"]["remaining_rows"] -= 1
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output = next(item for item in graph["outputs"] if item["path"] == "evidence/scenarios/closure-plan.json")
        output["digest"] = dependency.sha_file(plan_path)
        structure = next(item for item in graph["structures"] if item["id"] == fixture["structure_id"])
        structure["baseline_digest"] = dependency.structure_digest(structure["kind"], structure["path"], root, root / "evidence")
    else:
        raise AssertionError(f"unknown fixture mutation: {fixture['mutation']}")


def main() -> int:
    source_graph = json.loads((ROOT / "evidence/dependency-graph.json").read_text(encoding="utf-8"))
    baseline = json.loads((ROOT / "baseline/evidence-dependency-graph-v1.json").read_text(encoding="utf-8"))
    fixtures = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(FIXTURES.glob("*.yaml"))]
    for fixture in fixtures:
        with tempfile.TemporaryDirectory(prefix="rabbitmq-evidence-dependency-") as temporary:
            test_root = Path(temporary)
            copy_graph_tree(source_graph, test_root)
            graph = copy.deepcopy(source_graph)
            mutate(graph, fixture, test_root)
            errors = dependency.validate_graph(graph, test_root, baseline)
            if not any(fixture["expected_error"] in error for error in errors):
                raise AssertionError(f"{fixture['id']} was not rejected as expected: {errors[:8]}")
    print("Evidence dependency negative fixtures PASS: input変更/digest-only、rerun漏れ、output退避、Proof/Plan構造縮小を拒否")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
