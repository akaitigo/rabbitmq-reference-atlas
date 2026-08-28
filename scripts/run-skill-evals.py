#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/router-cases.json"
ROUTER = ROOT / ".agents/skills/rabbitmq-reference-router/scripts/route.py"


def main() -> int:
    suite = json.loads(CASES.read_text())
    results = []
    for case in suite["cases"]:
        completed = subprocess.run(
            [sys.executable, str(ROUTER), "--query", case["query"]],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        actual = json.loads(completed.stdout)
        passed = actual["mode"] == case["expected_mode"] and case["expected_reference"] in actual["references"]
        results.append({"id": case["id"], "passed": passed, "expected_mode": case["expected_mode"], "actual_mode": actual["mode"], "references": actual["references"]})
    passed_count = sum(1 for result in results if result["passed"])
    report = {
        "schema_version": 1,
        "suite": "rabbitmq-reference-router",
        "passed": passed_count,
        "total": len(results),
        "pass_rate": passed_count / len(results),
        "results": results,
    }
    evidence_root = pathlib.Path(os.environ.get("RABBITMQ_EVIDENCE_ROOT", ROOT / "evidence"))
    output = evidence_root / "raw/skill-eval.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["pass_rate"] != 1.0:
        return 1
    if os.environ.get("RABBITMQ_EVIDENCE_ONLY") == "1":
        return 0
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run-definitive-skill-eval.py")],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
