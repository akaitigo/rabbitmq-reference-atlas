#!/usr/bin/env python3
"""Validate the public-main baseline as an immutable floor."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from evidence_dependency_graph import BASELINE_PATH as DEPENDENCY_BASELINE_PATH
from evidence_dependency_graph import GRAPH_PATH as DEPENDENCY_GRAPH_PATH
from evidence_dependency_graph import validate_graph as validate_dependency_graph


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "baseline" / "public-main-22ab07c.yaml"
MIGRATION_PATH = ROOT / "migrations" / "public-main-baseline-v2.yaml"


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def index(rows: list[dict], label: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        row_id = row.get("id")
        if not row_id or row_id in result:
            raise ValueError(f"{label}: missing or duplicate id: {row_id}")
        result[row_id] = row
    return result


def contains_all(current: list, baseline: list) -> bool:
    return all(item in current for item in baseline)


def main() -> int:
    failures: list[str] = []
    baseline = load_yaml(BASELINE_PATH)
    migration = load_yaml(MIGRATION_PATH)
    coverage = load_yaml(ROOT / "coverage.yaml")
    sources = load_yaml(ROOT / "sources.lock.yaml")
    claims = load_yaml(ROOT / "atlas" / "claims" / "index.yaml")

    dependency_graph = json.loads(DEPENDENCY_GRAPH_PATH.read_text(encoding="utf-8"))
    dependency_baseline = json.loads(DEPENDENCY_BASELINE_PATH.read_text(encoding="utf-8"))
    for error in validate_dependency_graph(dependency_graph, ROOT, dependency_baseline):
        failures.append(f"evidence dependency additive baseline: {error}")

    baseline_targets = index(baseline["targets"], "baseline target")
    current_targets = index(coverage["targets"], "current target")
    replacements = {
        row["old_value"]: row for row in migration.get("replacements", [])
        if "old_value" in row
    }
    target_copy_replacements = {
        row["old_id"]: row for row in migration.get("replacements", [])
        if row.get("category") == "target-copy"
    }
    evidence_refresh = migration.get("evidence_refresh", {})
    refresh_proof_path = ROOT / evidence_refresh.get("proof", "")
    refresh_proof = json.loads(refresh_proof_path.read_text(encoding="utf-8")) if refresh_proof_path.is_file() else {}
    refresh_mappings = {row.get("path"): row for row in refresh_proof.get("mappings", [])}
    for target_id, old in baseline_targets.items():
        new = current_targets.get(target_id)
        if not new:
            failures.append(f"target deleted: {target_id}")
            continue
        for field in ("title", "target_set", "kind", "requirement", "rationale"):
            if new.get(field) != old.get(field):
                replacement_key = f"{target_id}#{field}"
                replacement = target_copy_replacements.get(replacement_key)
                if (
                    not replacement
                    or replacement.get("category") != "target-copy"
                    or replacement.get("old_id") != replacement_key
                    or replacement.get("new_id") != replacement_key
                    or replacement.get("new_value") != new.get(field)
                ):
                    failures.append(f"target {target_id} changed {field}")
                else:
                    proof_path = ROOT / replacement["runtime_proof"]["evidence"]
                    if not proof_path.is_file():
                        failures.append(f"target-copy proof missing: {proof_path.relative_to(ROOT)}")
                    else:
                        proof = json.loads(proof_path.read_text(encoding="utf-8"))
                        passed = {row["id"] for row in proof.get("checks", []) if row.get("verdict") == "pass"}
                        for check in replacement["runtime_proof"].get("required_checks", []):
                            if check not in passed:
                                failures.append(f"target-copy required check missing: {check}")
        if new.get("requirement") != "required":
            failures.append(f"target required downgrade: {target_id}")
        if new.get("state") != "covered":
            failures.append(f"target state downgrade: {target_id}={new.get('state')}")
        for field in ("claim_ids", "evidence_ids"):
            if not contains_all(new.get(field, []), old.get(field, [])):
                failures.append(f"target {target_id} reduced {field}")

    baseline_sources = index(baseline["sources"], "baseline source")
    current_sources = index(sources["sources"], "current source")
    for source_id, old in baseline_sources.items():
        if current_sources.get(source_id) != old:
            failures.append(f"source deleted or changed: {source_id}")

    baseline_claims = index(baseline["claims"], "baseline claim")
    current_claims = index(claims["claims"], "current claim")
    for claim_id, old in baseline_claims.items():
        if current_claims.get(claim_id) != old:
            failures.append(f"claim deleted or weakened: {claim_id}")

    artifact_counts: dict[str, int] = {}
    for group in ("proofs", "evidence", "labs", "go_tests"):
        rows = baseline["artifacts"][group]
        artifact_counts[group] = len(rows)
        for row in rows:
            path = ROOT / row["path"]
            if not path.is_file():
                failures.append(f"{group} artifact deleted: {row['path']}")
            elif sha256(path) != row["digest"]:
                if group != "evidence":
                    failures.append(f"{group} artifact changed without migration: {row['path']}")
                    continue
                mapping = refresh_mappings.get(row["path"])
                current = json.loads(path.read_text(encoding="utf-8"))
                if (
                    evidence_refresh.get("strength") != "stronger"
                    or refresh_proof.get("verdict") != "pass"
                    or mapping is None
                    or mapping.get("old_id") != row["id"]
                    or mapping.get("new_id") != row["id"]
                    or mapping.get("old_digest") != row["digest"]
                    or mapping.get("new_digest") != sha256(path)
                    or current.get("id") != row["id"]
                    or current.get("verdict") != "pass"
                    or not contains_all(current.get("claim_ids", []), row.get("claim_ids", []))
                    or current.get("producer") != row.get("producer")
                    or current.get("command") != row.get("command")
                    or not mapping.get("reason")
                ):
                    failures.append(f"evidence artifact changed without valid migration: {row['path']}")

    if refresh_mappings:
        expected_paths = {row["path"] for row in baseline["artifacts"]["evidence"] if sha256(ROOT / row["path"]) != row["digest"]}
        if set(refresh_mappings) != expected_paths:
            failures.append("evidence refresh migration mapping set is incomplete or excessive")
        run_report_path = ROOT / refresh_proof.get("runtime_proof", {}).get("run_report", {}).get("path", "")
        graph_path = ROOT / refresh_proof.get("runtime_proof", {}).get("dependency_graph", {}).get("path", "")
        if (not run_report_path.is_file()
                or sha256(run_report_path) != refresh_proof.get("runtime_proof", {}).get("run_report", {}).get("digest")
                or json.loads(run_report_path.read_text(encoding="utf-8")).get("status") != "full-run-passed"):
            failures.append("evidence refresh migration lacks current full-run publication proof")
        if (not graph_path.is_file()
                or sha256(graph_path) != refresh_proof.get("runtime_proof", {}).get("dependency_graph", {}).get("digest")
                or json.loads(graph_path.read_text(encoding="utf-8")).get("status") != "current"):
            failures.append("evidence refresh migration lacks current dependency graph proof")

    eval_baseline = baseline["artifacts"]["skill_eval"]
    eval_path = ROOT / eval_baseline["path"]
    if not eval_path.is_file() or sha256(eval_path) != eval_baseline["digest"]:
        failures.append("Router Skill eval baseline changed or deleted")
    else:
        current_cases = json.loads(eval_path.read_text(encoding="utf-8"))["cases"]
        if current_cases != eval_baseline["cases"]:
            failures.append("Router Skill eval cases were reduced or changed")

    workflow = load_yaml(ROOT / baseline["ci"]["path"])
    job = workflow.get("jobs", {}).get(baseline["ci"]["job"])
    if not job:
        failures.append("baseline CI job deleted: validate")
    else:
        if int(job.get("timeout-minutes", 0)) < int(baseline["ci"]["timeout_minutes"]):
            failures.append("CI timeout was reduced")
        if job.get("if") in (False, "false", "${{ false }}"):
            failures.append("baseline CI job disabled")
        current_steps = job.get("steps", [])
        named = {step.get("name"): step for step in current_steps if step.get("name")}
        uses = {step.get("uses") for step in current_steps if step.get("uses")}
        for old_step in baseline["ci"]["steps"]:
            if old_step.get("uses"):
                if old_step["uses"] not in uses:
                    replacement = replacements.get(old_step["uses"])
                    proof_path = ROOT / replacement.get("runtime_proof", {}).get("evidence", "") if replacement else ROOT
                    proof = json.loads(proof_path.read_text(encoding="utf-8")) if proof_path.is_file() else {}
                    mappings = {row.get("old"): row for row in proof.get("mappings", [])}
                    proof_mapping = mappings.get(old_step["uses"], {})
                    if (
                        not replacement
                        or replacement.get("category") != "ci-action-pin"
                        or replacement.get("new_value") not in uses
                        or replacement.get("runtime_proof", {}).get("strength") != "stronger"
                        or proof.get("verdict") != "pass"
                        or proof_mapping.get("new") != replacement.get("new_value")
                        or proof_mapping.get("strength") != "stronger"
                        or len(replacement.get("reason", "")) < 20
                        or len(proof_mapping.get("reason", "")) < 20
                    ):
                        failures.append(f"CI action deleted without stronger exact-pin migration: {old_step['uses']}")
                continue
            name = old_step["name"]
            new_step = named.get(name)
            if not new_step:
                failures.append(f"CI step deleted: {name}")
                continue
            if new_step.get("if") in (False, "false", "${{ false }}") or new_step.get("continue-on-error") is True:
                failures.append(f"CI step disabled or softened: {name}")
            if new_step.get("run") == old_step.get("run"):
                continue
            replacement = replacements.get(old_step.get("run"))
            if not replacement or replacement.get("new_value") != new_step.get("run"):
                failures.append(f"unmapped CI replacement: {name}")
                continue
            proof_path = ROOT / replacement["runtime_proof"]["evidence"]
            if not proof_path.is_file():
                failures.append(f"CI replacement proof missing: {proof_path.relative_to(ROOT)}")
            else:
                proof = json.loads(proof_path.read_text(encoding="utf-8"))
                if proof.get("verdict") != "pass":
                    failures.append(f"CI replacement proof not passing: {proof_path.relative_to(ROOT)}")
                passed = {row["id"] for row in proof.get("checks", []) if row.get("verdict") == "pass"}
                for check in replacement["runtime_proof"].get("required_checks", []):
                    if check not in passed:
                        failures.append(f"CI replacement required check missing: {check}")

    current_evidence = list(ROOT.glob("evidence/*.evidence.json"))
    current_labs = list(ROOT.glob("labs/*/lab.yaml"))
    current_tests = [
        path
        for path in ROOT.glob("**/*_test.go")
        if not any(part.startswith(".") or part == "vendor" for part in path.relative_to(ROOT).parts)
    ]
    definitive_eval_path = ROOT / "evals" / "rabbitmq-reference-atlas.definitive-skill-eval.json"
    definitive_eval_cases = 0
    if definitive_eval_path.is_file():
        definitive_eval_cases = len(json.loads(definitive_eval_path.read_text(encoding="utf-8"))["cases"])
    current_counts = {
        "targets": len(current_targets),
        "sources": len(current_sources),
        "claims": len(current_claims) + len(list((ROOT / "claims").glob("*.claim.yaml"))),
        "evidence_records": len(current_evidence),
        "labs": len(current_labs),
        "go_test_files": len(current_tests),
        "skill_eval_cases": len(json.loads(eval_path.read_text(encoding="utf-8"))["cases"]) + definitive_eval_cases,
    }
    for field in ("targets", "sources", "claims", "evidence_records", "labs", "go_test_files", "skill_eval_cases"):
        old_count = baseline["counts"][field]
        if current_counts[field] < old_count:
            failures.append(f"count regression {field}: {current_counts[field]} < {old_count}")

    status = "FAIL" if failures else "PASS"
    print(
        "non-regression " + status + ": "
        + " ".join(
            f"{key}={baseline['counts'][key]}->{current_counts.get(key, baseline['counts'][key])}"
            for key in ("targets", "sources", "claims", "evidence_records", "labs", "go_test_files", "skill_eval_cases")
        )
    )
    print(
        "baseline artifacts preserved: "
        + " ".join(f"{key}={value}" for key, value in artifact_counts.items())
    )
    for failure in failures:
        print(f"ERROR: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
