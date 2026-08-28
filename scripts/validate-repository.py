#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import re
import sys

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def sha(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: pathlib.Path):
    return yaml.safe_load(path.read_text())


def raw_checks(document):
    checks = []
    for run in document.get("runs", [document]):
        run_checks = run.get("checks", [])
        if isinstance(run_checks, dict):
            checks.extend({"name": name, "passed": passed} for name, passed in run_checks.items())
        elif isinstance(run_checks, list):
            checks.extend(run_checks)
    return checks


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Core Schema外の横断Gateを検証する")
    parser.add_argument("--release", action="store_true", help="Completion Certificateを要求する")
    args = parser.parse_args()
    errors: list[str] = []
    atlas = load(ROOT / "atlas.yaml")
    sources = load(ROOT / "sources.lock.yaml")
    coverage = load(ROOT / "coverage.yaml")
    mastery = load(ROOT / "mastery.yaml")
    skill = load(ROOT / "skill.package.yaml")
    claims_doc = load(ROOT / "atlas/claims/index.yaml")
    claims = {item["id"] for item in claims_doc["claims"]}

    for name, document in (("sources", sources), ("coverage", coverage), ("mastery", mastery), ("skill", skill)):
        if document["atlas_id"] != atlas["id"]:
            fail(errors, f"{name}.atlas_id mismatch")
    if sources["epoch"] != coverage["epoch"] or sources["epoch"] != atlas["coverage"]["epoch"]:
        fail(errors, "coverage epoch mismatch")
    if skill["router"]["id"] != atlas["skills"]["router"]["id"] or skill["router"]["path"] != atlas["skills"]["router"]["path"]:
        fail(errors, "router manifest mismatch")
    if not (ROOT / skill["router"]["path"]).exists():
        fail(errors, "router path missing")
    expected_lock_digest = sha(ROOT / "sources.lock.yaml")
    if coverage["authority_lock_digest"] != expected_lock_digest:
        fail(errors, f"authority lock digest mismatch: expected {expected_lock_digest}")
    if any(source["digest"].endswith("0" * 64) or not SHA256.match(source["digest"]) for source in sources["sources"]):
        fail(errors, "source lock contains placeholder or invalid digest")

    target_sets = {item["id"] for item in coverage["target_sets"]}
    for collection in ("outcomes", "surfaces"):
        for item in mastery[collection]:
            for target_set in item["target_sets"]:
                if target_set not in target_sets:
                    fail(errors, f"mastery {collection} {item['id']} references unknown target_set {target_set}")
    evidence_records = {}
    for path in sorted((ROOT / "evidence").glob("*.evidence.json")):
        record = json.loads(path.read_text())
        evidence_records[record["id"]] = record
        artifact = ROOT / record["artifact"]["uri"]
        if not artifact.exists() or sha(artifact) != record["artifact"]["digest"]:
            fail(errors, f"artifact digest mismatch: {record['id']}")
        elif record["artifact"].get("media_type") == "application/json":
            try:
                raw = json.loads(artifact.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError):
                fail(errors, f"invalid JSON artifact: {record['id']}")
            else:
                checks = raw_checks(raw)
                if checks and any(check.get("passed") is not True for check in checks):
                    fail(errors, f"failed raw oracle in pass evidence: {record['id']}")
                if record["kind"] == "skill-eval":
                    results = raw.get("results", [])
                    if not results or not all(item.get("passed") is True for item in results) or raw.get("passed") != raw.get("total"):
                        fail(errors, f"incomplete skill eval result: {record['id']}")
        if record["source_digest"] != expected_lock_digest:
            fail(errors, f"source digest mismatch: {record['id']}")
    for target in coverage["targets"]:
        if target["target_set"] not in target_sets:
            fail(errors, f"unknown target_set: {target['id']}")
        for claim in target["claim_ids"]:
            if claim not in claims:
                fail(errors, f"unknown claim {claim} in {target['id']}")
        for evidence_id in target["evidence_ids"]:
            record = evidence_records.get(evidence_id)
            if record is None:
                fail(errors, f"unknown evidence {evidence_id} in {target['id']}")
            elif not set(target["claim_ids"]).intersection(record["claim_ids"]):
                fail(errors, f"evidence {evidence_id} is not connected to {target['id']}")
            elif record.get("verdict") != "pass":
                fail(errors, f"non-pass evidence {evidence_id} connected to covered target {target['id']}")
        if target["state"] == "covered" and (not target["claim_ids"] or not target["evidence_ids"]):
            fail(errors, f"covered target lacks claim/evidence: {target['id']}")
    required = ["LICENSE", "NOTICE", "SECURITY.md", "CONTRIBUTING.md", "third_party/manifest.yaml", "sbom.spdx.json", "third_party/sbom.cdx.json"]
    for relative in required:
        if not (ROOT / relative).exists():
            fail(errors, f"required publication file missing: {relative}")
    forbidden = ("BEGIN PRIVATE KEY", "ghp_", "github_pat_", "AKIA")
    for path in ROOT.rglob("*"):
        if path.is_file() and ".git" not in path.parts and ".cache" not in path.parts and path.stat().st_size < 2_000_000:
            if path == pathlib.Path(__file__).resolve():
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            for token in forbidden:
                if token in text:
                    fail(errors, f"secret-like token {token!r} in {path.relative_to(ROOT)}")
    incomplete_targets = [target["id"] for target in coverage["targets"] if target["requirement"] == "required" and target["state"] not in ("covered", "excluded", "infeasible")]
    if incomplete_targets and atlas["status"] != "incomplete":
        fail(errors, "status must remain incomplete while required targets are open")
    if args.release:
        certificate = ROOT / atlas["completion"]["certificate"]
        if atlas["status"] != "complete":
            fail(errors, "release validation requires status complete")
        if not certificate.exists():
            fail(errors, "completion certificate missing")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"横断Gate通過: targets={len(coverage['targets'])}, evidence={len(evidence_records)}, status={atlas['status']}")
    if atlas["status"] == "incomplete":
        print("Release Gateは未申請です。status: incompleteを維持しています。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
