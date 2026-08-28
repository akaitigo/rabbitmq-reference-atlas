#!/usr/bin/env python3
"""Capture the immutable public-main non-regression baseline.

This script is intentionally maintainer-only: CI validates the committed snapshot
and never regenerates it from a possibly shallow checkout.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "22ab07cc6c3d92ab489fe6ff8855c9fb8a97db5a"
OUTPUT = ROOT / "baseline" / "public-main-22ab07c.yaml"


def git_bytes(path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{BASE_COMMIT}:{path}"], cwd=ROOT
    )


def git_text(path: str) -> str:
    return git_bytes(path).decode("utf-8")


def git_yaml(path: str):
    return yaml.safe_load(git_text(path))


def git_json(path: str):
    return json.loads(git_text(path))


def digest(path: str) -> str:
    return "sha256:" + hashlib.sha256(git_bytes(path)).hexdigest()


def files_matching(pattern: str) -> list[str]:
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", BASE_COMMIT], cwd=ROOT, text=True
    ).splitlines()
    regex = re.compile(pattern)
    return sorted(path for path in paths if regex.search(path))


def hashed_files(paths: list[str], id_loader=None) -> list[dict]:
    rows = []
    for path in paths:
        row = {"path": path, "digest": digest(path)}
        if id_loader:
            row.update(id_loader(path))
        rows.append(row)
    return rows


def evidence_identity(path: str) -> dict:
    value = git_json(path)
    return {
        "id": value["id"],
        "claim_ids": value.get("claim_ids", []),
        "verdict": value.get("verdict"),
        "producer": value.get("producer"),
        "command": value.get("command"),
    }


def lab_identity(path: str) -> dict:
    value = git_yaml(path)
    return {"id": value["id"]}


def proof_identity(path: str) -> dict:
    value = git_yaml(path)
    obligations = value.get("proof_obligations", value.get("obligations", []))
    if isinstance(obligations, dict):
        ids = list(obligations)
    else:
        ids = [item["id"] for item in obligations]
    return {"ids": ids}


def test_identity(path: str) -> dict:
    names = re.findall(r"^func (Test[A-Za-z0-9_]+)\(", git_text(path), re.MULTILINE)
    return {"test_functions": names}


def main() -> None:
    coverage = git_yaml("coverage.yaml")
    sources = git_yaml("sources.lock.yaml")
    claims = git_yaml("atlas/claims/index.yaml")
    evals = git_json("evals/router-cases.json")
    workflow = git_yaml(".github/workflows/validate.yaml")

    evidence_paths = files_matching(r"^evidence/[^/]+\.evidence\.json$")
    lab_paths = files_matching(r"^labs/[^/]+/lab\.yaml$")
    proof_paths = files_matching(r"^atlas/proof-obligations/[^/]+\.yaml$")
    test_paths = files_matching(r"_test\.go$")

    validate_job = workflow["jobs"]["validate"]
    ci_steps = []
    for step in validate_job["steps"]:
        ci_steps.append(
            {
                "name": step.get("name"),
                "uses": step.get("uses"),
                "run": step.get("run"),
            }
        )

    baseline = {
        "schema_version": 1,
        "id": "public-main-22ab07c",
        "source_commit": BASE_COMMIT,
        "captured_from": "refs/heads/main",
        "policy": {
            "state": "immutable-non-regression-baseline",
            "replacement_contract": "migrations/public-main-baseline-v2.yaml",
            "forbidden": [
                "delete",
                "skip-or-disable",
                "required-downgrade",
                "excluded-or-infeasible-retreat",
                "coarse-aggregation",
                "assertion-or-threshold-reduction",
                "protocol-plugin-version-or-ci-reduction",
                "real-runtime-to-mock-or-static",
                "failure-evidence-deletion",
            ],
        },
        "counts": {
            "targets": len(coverage["targets"]),
            "sources": len(sources["sources"]),
            "claims": len(claims["claims"]),
            "proof_files": len(proof_paths),
            "evidence_records": len(evidence_paths),
            "labs": len(lab_paths),
            "go_test_files": len(test_paths),
            "skill_eval_cases": len(evals["cases"]),
            "ci_steps": len(ci_steps),
        },
        "targets": coverage["targets"],
        "sources": sources["sources"],
        "claims": claims["claims"],
        "artifacts": {
            "proofs": hashed_files(proof_paths, proof_identity),
            "evidence": hashed_files(evidence_paths, evidence_identity),
            "labs": hashed_files(lab_paths, lab_identity),
            "go_tests": hashed_files(test_paths, test_identity),
            "skill_eval": {
                "path": "evals/router-cases.json",
                "digest": digest("evals/router-cases.json"),
                "cases": evals["cases"],
            },
        },
        "ci": {
            "path": ".github/workflows/validate.yaml",
            "job": "validate",
            "timeout_minutes": validate_job["timeout-minutes"],
            "steps": ci_steps,
        },
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        yaml.safe_dump(baseline, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    print(
        f"captured {OUTPUT.relative_to(ROOT)}: "
        f"targets={baseline['counts']['targets']} "
        f"sources={baseline['counts']['sources']} "
        f"evidence={baseline['counts']['evidence_records']} "
        f"labs={baseline['counts']['labs']}"
    )


if __name__ == "__main__":
    main()
