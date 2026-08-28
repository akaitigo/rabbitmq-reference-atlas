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


def historical_authority_digests() -> set[str]:
    digests: set[str] = set()
    history = ROOT / "evidence/historical/index.yaml"
    if not history.exists():
        return digests
    for record in load(history).get("records", []):
        certificate_path = ROOT / record["source_path"]
        if not certificate_path.exists():
            continue
        certificate = json.loads(certificate_path.read_text())
        digest = certificate.get("manifests", {}).get("sources.lock.yaml")
        if digest:
            digests.add(digest)
    return digests


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
    parity = load(ROOT / "rabbitmq-depth-parity.yaml")
    claims_doc = load(ROOT / "atlas/claims/index.yaml")
    claims = {item["id"] for item in claims_doc["claims"]}
    claims.update(
        load(claim_path)["id"]
        for claim_path in sorted((ROOT / "claims").glob("*.claim.yaml"))
    )
    historical_digests = historical_authority_digests()

    for name, document in (("sources", sources), ("coverage", coverage), ("mastery", mastery), ("skill", skill)):
        if document["atlas_id"] != atlas["id"]:
            fail(errors, f"{name}.atlas_id mismatch")
    if sources["epoch"] != coverage["epoch"] or sources["epoch"] != atlas["coverage"]["epoch"]:
        fail(errors, "coverage epoch mismatch")
    if skill["router"]["id"] != atlas["skills"]["router"]["id"] or skill["router"]["path"] != atlas["skills"]["router"]["path"]:
        fail(errors, "router manifest mismatch")
    parity_gap_total = sum(axis["gap_count"] for axis in parity["axes"])
    if parity["summary"]["total_gaps"] != parity_gap_total:
        fail(errors, "RabbitMQ depth parity gap total mismatch")
    if parity["summary"]["definitive_allowed"] != (parity_gap_total == 0):
        fail(errors, "RabbitMQ depth parity definitive decision mismatch")
    if parity["status"] != ("complete" if parity_gap_total == 0 else "incomplete"):
        fail(errors, "RabbitMQ depth parity status mismatch")
    if atlas["status"] == "complete" and parity_gap_total != 0:
        fail(errors, "status complete is forbidden while RabbitMQ depth parity gaps remain")
    expected_parity_axes = {
        "authority-body-digestion", "surface-atomic-behavior-variant", "real-runtime-lab",
        "scenario-normal", "scenario-boundary", "scenario-refusal", "scenario-failure",
        "scenario-recovery", "scenario-migration", "scenario-operations", "scenario-security",
        "scenario-performance", "scenario-compatibility", "artifact-trace",
        "integrated-reference-system", "skill-eval", "rights-provenance", "non-regression-gate",
    }
    if len(parity["axes"]) != 18 or {axis["id"] for axis in parity["axes"]} != expected_parity_axes:
        fail(errors, "RabbitMQ depth parity must preserve the FE reference's exact 18 axes")
    if parity["reference"]["source_commit"] != "4a0b2df8e2091a963bd0e0e1bbccef9c84b49a45":
        fail(errors, "RabbitMQ depth parity uses an unexpected FE reference commit")
    if parity["reference"]["source_summary"] != {"satisfied": 1, "partial": 17, "missing": 0}:
        fail(errors, "FE reference status must remain 1/18 satisfied and incomplete")
    locator_reference = load(ROOT / "parity/frontend-depth-reference.yaml")["authority_extraction_reference"]
    if locator_reference["source_commit"] != "cabf687bab769b17928d950acc416f3f77eb4ca3":
        fail(errors, "RabbitMQ Authority locator audit uses an unexpected FE reference commit")
    if locator_reference["body_storage"] != "digest-and-locator-context-digest-only" or locator_reference["human_reviewed_surfaces"] != 0:
        fail(errors, "RabbitMQ Authority locator copyright/review boundary mismatch")
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
    current_evidence = 0
    historical_evidence = 0
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
        if record["source_digest"] == expected_lock_digest:
            current_evidence += 1
        elif record["source_digest"] in historical_digests:
            historical_evidence += 1
        else:
            fail(errors, f"source digest mismatch: {record['id']}")
    for target in coverage["targets"]:
        if target["target_set"] not in target_sets:
            fail(errors, f"unknown target_set: {target['id']}")
        for claim in target["claim_ids"]:
            if target["state"] == "covered" and claim not in claims:
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

    inventory_path = ROOT / "surface.inventory.yaml"
    plan_path = ROOT / "verification.plan.yaml"
    if atlas["completion"].get("policy_version", "").startswith("2."):
        definitive = load(ROOT / atlas["completion"]["definitive"]["manifest"])
        inventory = load(inventory_path)
        plan = load(plan_path)
        source_classification = load(ROOT / "surface/source-classification.yaml")
        if inventory["authority_lock_digest"] != expected_lock_digest:
            fail(errors, "surface inventory authority digest mismatch")
        classified_sources = [item["source_id"] for item in source_classification["sources"]]
        locked_sources = [item["id"] for item in sources["sources"]]
        if sorted(classified_sources) != sorted(locked_sources) or len(classified_sources) != len(set(classified_sources)):
            fail(errors, "authority source classification is incomplete or duplicated")
        artifact_source_map = {item["id"]: item["source_id"] for item in inventory["authority_artifacts"]}
        for item in source_classification["sources"]:
            artifact_id = item["authority_artifact_id"]
            if item["classification"] == "surface-authority" and artifact_source_map.get(artifact_id) != item["source_id"]:
                fail(errors, f"surface authority classification mismatch: {item['source_id']}")
            if item["classification"] == "supporting-authority" and artifact_id is not None:
                fail(errors, f"supporting authority unexpectedly links artifact: {item['source_id']}")
        inventory_behaviors = {}
        inventory_targets = {}
        definitive_claims = {}
        for claim_path in sorted((ROOT / "claims").glob("*.claim.yaml")):
            claim = load(claim_path)
            definitive_claims[claim["id"]] = claim
        target_index = {target["id"]: target for target in coverage["targets"]}
        extracted = set()
        for artifact in inventory["authority_artifacts"]:
            artifact_path = ROOT / artifact["path"]
            if not artifact_path.exists() or sha(artifact_path) != artifact["digest"]:
                fail(errors, f"authority artifact digest mismatch: {artifact['id']}")
                continue
            authority = load(artifact_path)
            source = next((item for item in sources["sources"] if item["id"] == artifact["source_id"]), None)
            if source is None or authority["source_digest"] != source["digest"]:
                fail(errors, f"authority artifact source mismatch: {artifact['id']}")
            for surface in authority["surfaces"]:
                extracted.add((artifact["id"], surface["id"]))
        classified = set()
        for item in inventory["items"]:
            behavior_id = item["behavior_id"]
            target_id = item["target_id"]
            key = (item["authority_artifact_id"], item["authority_surface_id"])
            classified.add(key)
            if behavior_id in inventory_behaviors:
                fail(errors, f"duplicate definitive behavior: {behavior_id}")
            if target_id in inventory_targets:
                fail(errors, f"aggregate definitive target: {target_id}")
            inventory_behaviors[behavior_id] = item
            inventory_targets[target_id] = behavior_id
            target = target_index.get(target_id)
            if target is None or target["requirement"] != "required":
                fail(errors, f"definitive behavior lacks required target: {behavior_id}")
            elif target["claim_ids"] != item["claim_ids"]:
                fail(errors, f"definitive target claim mismatch: {target_id}")
            claim = definitive_claims.get(item["claim_ids"][0])
            if claim is None or claim["capability_id"] != item["capability_id"]:
                fail(errors, f"definitive behavior lacks dedicated claim: {behavior_id}")
            elif target["state"] != "covered" and claim["status"] != "proposed":
                fail(errors, f"open definitive claim must remain proposed: {claim['id']}")
            elif target["state"] == "covered" and claim["status"] != "accepted":
                fail(errors, f"covered definitive claim must be accepted: {claim['id']}")
        if extracted != classified:
            fail(errors, f"authority inventory classification mismatch: extracted={len(extracted)} classified={len(classified)}")
        rows = {(row["behavior_id"], row["scenario"]): row for row in plan["rows"]}
        scenarios = {"normal", "boundary", "rejection", "failure", "recovery", "migration", "operations", "security", "performance", "compatibility"}
        required_by_surface = {
            "failure-recovery": {"failure", "recovery"},
            "operations-observability": {"operations"},
            "security-privacy-safety": {"security"},
            "performance-capacity-cost": {"performance"},
            "compatibility-integration": {"compatibility"},
            "migration-evolution-deprecation": {"migration"},
        }
        for behavior_id, item in inventory_behaviors.items():
            required_scenarios = {"normal", "boundary", "rejection"}
            for surface_id in item["surface_ids"]:
                required_scenarios.update(required_by_surface.get(surface_id, set()))
            for scenario in scenarios:
                row = rows.get((behavior_id, scenario))
                if row is None:
                    fail(errors, f"verification plan row missing: {behavior_id}:{scenario}")
                elif scenario in required_scenarios and row["applicability"] != "required":
                    fail(errors, f"required verification scenario disabled: {behavior_id}:{scenario}")
            claim = definitive_claims.get(item["claim_ids"][0])
            if claim:
                expected_proofs = {
                    rows[(behavior_id, scenario)]["proof_obligation_id"]
                    for scenario in required_scenarios
                }
                actual_proofs = {proof["id"] for proof in claim["proof_obligations"]}
                if actual_proofs != expected_proofs:
                    fail(errors, f"claim proof plan mismatch: {claim['id']}")
        if len(rows) != len(inventory_behaviors) * len(scenarios):
            fail(errors, "verification plan has duplicate or extra rows")
        if definitive["surface_inventory"] != inventory_path.name or definitive["verification_matrix"] != "verification.matrix.yaml":
            fail(errors, "definitive manifest path mismatch")
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
    print(f"横断Gate通過: targets={len(coverage['targets'])}, evidence_current={current_evidence}, evidence_historical={historical_evidence}, status={atlas['status']}")
    if atlas["status"] == "incomplete":
        print("Release Gateは未申請です。status: incompleteを維持しています。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
