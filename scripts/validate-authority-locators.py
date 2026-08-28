#!/usr/bin/env python3
"""copyright-safe Authority locator Artifactと未完了境界を検証する。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "authority/extraction.snapshot.json"
DIRECTORY = ROOT / "authority/locator-drafts"
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def exact(value: dict, keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise SystemExit(f"{label}: 本文field、未知field、または必須field欠落: {sorted(set(value) ^ keys)}")


def main() -> None:
    sources = yaml.safe_load((ROOT / "sources.lock.yaml").read_text(encoding="utf-8"))["sources"]
    source_by_id = {item["id"]: item for item in sources}
    expected_edges: dict[str, dict] = {}
    edges_by_source: dict[str, list[dict]] = {}
    for authority_path in sorted((ROOT / "surface/authority").glob("*.authority-surfaces.yaml")):
        authority = yaml.safe_load(authority_path.read_text(encoding="utf-8"))
        source = source_by_id[authority["source_id"]]
        for surface in authority["surfaces"]:
            edge = {
                "edge_id": f"edge.{surface['behavior_id']}.{authority['source_id']}", "source_id": authority["source_id"],
                "reference_url": source["url"] + surface["locator"], "locator": surface["locator"],
                "authority_surface_id": surface["id"], "candidate_behavior_id": f"candidate.{surface['behavior_id']}",
                "capability_id": surface["capability_id"], "target_id": f"definitive.{surface['behavior_id']}",
                "claim_id": f"definitive.{surface['behavior_id']}.claim", "surface_ids": sorted(surface["surface_ids"]),
                "classification_basis": "existing-surface-projection-unreviewed",
                "domain_metadata_digest": sha(canonical({"kind": surface["kind"], "title": surface["title"]})),
            }
            expected_edges[edge["edge_id"]] = edge
            edges_by_source.setdefault(authority["source_id"], []).append(edge)
    for edges in edges_by_source.values():
        edges.sort(key=lambda item: item["edge_id"])
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    exact(index, {"schema_version", "atlas_id", "generated_at", "status", "input_digest", "body_storage", "summary", "denominators", "sources"}, "Authority index")
    exact(index["summary"], {"locked_sources", "fetched_digest_matched", "fetched_digest_stale", "fetch_failed", "candidate_surfaces",
          "root_locators", "fragments_found", "fragments_not_found", "locator_evaluations_deferred", "reference_edges_classified",
          "unclassified_reference_edges", "body_structure_sources_scanned", "body_structure_sources_deferred", "body_section_candidates",
          "authority_text_surfaces_exhaustive", "human_reviewed_surfaces", "core_v2_eligible_surfaces"}, "Authority summary")
    if index["atlas_id"] != "rabbitmq-reference-atlas" or index["status"] != "incomplete-human-review-required":
        raise SystemExit("Authority index status/identity mismatch")
    if index["body_storage"] != "digest-and-locator-context-digest-only":
        raise SystemExit("Authority body storage boundary mismatch")
    expected_input_digest = sha(canonical({
        "sources": [{"id": item["id"], "url": item["url"], "digest": item["digest"], "version": item["version"]} for item in sources],
        "reference_edges": edges_by_source,
    }))
    if index["input_digest"] != expected_input_digest:
        raise SystemExit("Authority extraction input drift")
    expected_files = {f"{item['id']}.json" for item in sources}
    actual_files = {path.name for path in DIRECTORY.glob("*.json")}
    if actual_files != expected_files:
        raise SystemExit("Authority locator Artifact集合がSource lockと一致しません")
    counters = Counter()
    locator = Counter()
    candidates = 0
    records = {item["id"]: item for item in index["sources"]}
    for path in sorted(DIRECTORY.glob("*.json")):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        exact(artifact, {"schema_version", "source_id", "source_url", "locked_source_digest", "fetch", "extraction", "body_structure", "candidate_surfaces"}, path.name)
        exact(artifact["fetch"], {"status", "fetched_digest", "locked_digest_match", "http_status", "final_url", "content_type", "fetched_bytes", "error_digest"}, f"{path.name} fetch")
        exact(artifact["extraction"], {"method", "tool", "review_status", "body_storage"}, f"{path.name} extraction")
        exact(artifact["body_structure"], {"status", "method", "review_status", "body_storage", "sections"}, f"{path.name} body structure")
        source = source_by_id.get(artifact["source_id"])
        if not source or artifact["source_url"] != source["url"] or artifact["locked_source_digest"] != source["digest"]:
            raise SystemExit(f"Authority source identity mismatch: {path.name}")
        if artifact["extraction"] != {"method": "locked-body-locator-context-digest", "tool": "rabbitmq-reference-atlas-authority-extractor-v1",
                                      "review_status": "automated-unreviewed", "body_storage": "digest-and-locator-context-digest-only"}:
            raise SystemExit(f"Authority extraction/review boundary mismatch: {path.name}")
        status = artifact["fetch"]["status"]
        counters[status] += 1
        if status == "matched" and (not artifact["fetch"]["locked_digest_match"] or artifact["fetch"]["fetched_digest"] != source["digest"]):
            raise SystemExit(f"Matched digest mismatch: {path.name}")
        if status == "stale" and (artifact["fetch"]["locked_digest_match"] or artifact["fetch"]["fetched_digest"] in {None, source["digest"]}):
            raise SystemExit(f"Stale digest boundary mismatch: {path.name}")
        if status == "failed" and (artifact["fetch"]["fetched_digest"] is not None or not SHA.match(artifact["fetch"]["error_digest"] or "")):
            raise SystemExit(f"Failed fetch boundary mismatch: {path.name}")
        expected_structure_status = "structurally-scanned" if status == "matched" else ("not-evaluated-stale-body" if status == "stale" else "not-evaluated-fetch-failed")
        if artifact["body_structure"]["status"] != expected_structure_status or artifact["body_structure"]["method"] != "markdown-section-byte-partition-v1" or artifact["body_structure"]["review_status"] != "automated-unreviewed" or artifact["body_structure"]["body_storage"] != "digest-and-locator-context-digest-only":
            raise SystemExit(f"Authority body structure boundary mismatch: {path.name}")
        previous_end = 0
        for section in artifact["body_structure"]["sections"]:
            exact(section, {"section_id", "locator", "level", "section_start", "section_end", "context_unit", "section_digest", "heading_digest", "classification"}, f"{path.name} body section")
            if section["section_start"] != previous_end or section["section_end"] <= section["section_start"] or section["context_unit"] != "utf8-byte" or not SHA.match(section["section_digest"]) or section["classification"] != "automated-section-unreviewed":
                raise SystemExit(f"Authority body section partition mismatch: {section['section_id']}")
            if section["heading_digest"] is not None and not SHA.match(section["heading_digest"]):
                raise SystemExit(f"Authority body heading digest mismatch: {section['section_id']}")
            previous_end = section["section_end"]
        if status == "matched" and (not artifact["body_structure"]["sections"] or previous_end != artifact["fetch"]["fetched_bytes"]):
            raise SystemExit(f"Authority body structure does not cover fetched bytes: {path.name}")
        if status != "matched" and artifact["body_structure"]["sections"]:
            raise SystemExit(f"Stale/failed Authority body must not be structurally scanned: {path.name}")
        for candidate in artifact["candidate_surfaces"]:
            exact(candidate, {"edge_id", "source_id", "reference_url", "locator", "authority_surface_id", "candidate_behavior_id",
                  "capability_id", "target_id", "claim_id", "surface_ids", "classification_basis", "domain_metadata_digest",
                  "locator_status", "context_digest", "context_start", "context_end", "context_unit", "heading_digest", "classification"},
                  f"{path.name} candidate")
            if candidate["source_id"] != artifact["source_id"] or candidate["classification"] != "candidate-included-unreviewed":
                raise SystemExit(f"Authority candidate identity/review mismatch: {candidate['edge_id']}")
            expected = expected_edges.get(candidate["edge_id"])
            if expected is None or any(candidate[key] != value for key, value in expected.items()):
                raise SystemExit(f"Authority reference edge drift: {candidate['edge_id']}")
            located = candidate["locator_status"] in {"root-document", "fragment-found"}
            has_context = (SHA.match(candidate["context_digest"] or "") is not None and isinstance(candidate["context_start"], int)
                           and isinstance(candidate["context_end"], int) and candidate["context_unit"] == "utf8-byte")
            if located != has_context:
                raise SystemExit(f"Authority locator context mismatch: {candidate['edge_id']}")
            if status == "matched" and candidate["locator_status"].startswith("not-evaluated-"):
                raise SystemExit(f"Matched Authority locator deferred: {candidate['edge_id']}")
            if status == "stale" and candidate["locator_status"] != "not-evaluated-stale-body":
                raise SystemExit(f"Stale Authority locator mismatch: {candidate['edge_id']}")
            if status == "failed" and candidate["locator_status"] != "not-evaluated-fetch-failed":
                raise SystemExit(f"Failed Authority locator mismatch: {candidate['edge_id']}")
            locator[candidate["locator_status"]] += 1
            candidates += 1
        record = records.get(artifact["source_id"])
        exact(record, {"id", "path", "digest", "locked_digest_match", "candidate_surfaces", "body_structure_status", "body_sections", "locator_status"}, f"{path.name} index")
        expected_locator = dict(sorted(Counter(item["locator_status"] for item in artifact["candidate_surfaces"]).items()))
        if record != {"id": artifact["source_id"], "path": path.relative_to(ROOT).as_posix(), "digest": sha(path.read_bytes()),
                      "locked_digest_match": artifact["fetch"]["locked_digest_match"], "candidate_surfaces": len(artifact["candidate_surfaces"]),
                      "body_structure_status": artifact["body_structure"]["status"], "body_sections": len(artifact["body_structure"]["sections"]),
                      "locator_status": expected_locator}:
            raise SystemExit(f"Authority index record mismatch: {path.name}")
    all_artifacts = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(DIRECTORY.glob("*.json"))]
    expected_summary = {"locked_sources": len(sources), "fetched_digest_matched": counters["matched"], "fetched_digest_stale": counters["stale"],
                        "fetch_failed": counters["failed"], "candidate_surfaces": candidates, "root_locators": locator["root-document"],
                        "fragments_found": locator["fragment-found"], "fragments_not_found": locator["fragment-not-found"],
                        "locator_evaluations_deferred": locator["not-evaluated-stale-body"] + locator["not-evaluated-fetch-failed"],
                        "reference_edges_classified": candidates, "unclassified_reference_edges": 0,
                        "body_structure_sources_scanned": sum(item["body_structure"]["status"] == "structurally-scanned" for item in all_artifacts),
                        "body_structure_sources_deferred": sum(item["body_structure"]["status"] != "structurally-scanned" for item in all_artifacts),
                        "body_section_candidates": sum(len(item["body_structure"]["sections"]) for item in all_artifacts),
                        "authority_text_surfaces_exhaustive": False, "human_reviewed_surfaces": 0, "core_v2_eligible_surfaces": 0}
    if index["summary"] != expected_summary:
        raise SystemExit("Authority summary mismatch")
    for name in ("protocol", "plugin", "operator"):
        item = index["denominators"][name]
        exact(item, {"source_ids", "sources", "body_exhaustive", "human_reviewed_surfaces", "status"}, f"{name} denominator")
        if item["sources"] != len(item["source_ids"]) or item["body_exhaustive"] != 0 or item["human_reviewed_surfaces"] != 0 or item["status"] != "partial":
            raise SystemExit(f"{name} Authority denominator cannot be closed")
    print(f"authority-locators PASS: matched={counters['matched']}/{len(sources)} stale={counters['stale']} failed={counters['failed']} candidates={candidates} sections={expected_summary['body_section_candidates']} deferred={expected_summary['locator_evaluations_deferred']} human_reviewed=0 exhaustive=false")


if __name__ == "__main__":
    main()
