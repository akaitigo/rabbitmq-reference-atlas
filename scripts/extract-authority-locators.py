#!/usr/bin/env python3
"""Authority本文を保存せず、固定bodyのLocator offsetとdigestだけを生成する。"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "authority/locator-drafts"
INDEX = ROOT / "authority/extraction.snapshot.json"
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def authority_edges() -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for path in sorted((ROOT / "surface/authority").glob("*.authority-surfaces.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for surface in document["surfaces"]:
            locator = surface["locator"]
            edge = {
                "edge_id": f"edge.{surface['behavior_id']}.{document['source_id']}",
                "source_id": document["source_id"],
                "reference_url": next(item["url"] for item in yaml.safe_load((ROOT / "sources.lock.yaml").read_text())["sources"] if item["id"] == document["source_id"]) + locator,
                "locator": locator,
                "authority_surface_id": surface["id"],
                "candidate_behavior_id": f"candidate.{surface['behavior_id']}",
                "capability_id": surface["capability_id"],
                "target_id": f"definitive.{surface['behavior_id']}",
                "claim_id": f"definitive.{surface['behavior_id']}.claim",
                "surface_ids": sorted(surface["surface_ids"]),
                "classification_basis": "existing-surface-projection-unreviewed",
                "domain_metadata_digest": sha(canonical({"kind": surface["kind"], "title": surface["title"]})),
            }
            result.setdefault(document["source_id"], []).append(edge)
    for edges in result.values():
        edges.sort(key=lambda item: item["edge_id"])
    return result


def slug(value: str) -> str:
    value = re.sub(r"\{#[^}]+\}\s*$", "", value).strip().lower()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    return re.sub(r"[\s-]+", "-", value).strip("-")


def locate(body: bytes, locator: str) -> dict:
    if locator in {"", "document-root", "#"}:
        return {"locator_status": "root-document", "context_digest": sha(body), "context_start": 0,
                "context_end": len(body), "context_unit": "utf8-byte", "heading_digest": None}
    fragment = locator.removeprefix("#")
    patterns = [
        re.compile(rb"(?:id|name)\s*=\s*['\"]" + re.escape(fragment.encode()) + rb"['\"]", re.I),
        re.compile(rb"\{#" + re.escape(fragment.encode()) + rb"\}"),
    ]
    match = next((found for pattern in patterns if (found := pattern.search(body))), None)
    heading_line = None
    if match is None:
        for candidate in re.finditer(rb"(?m)^#{1,6}\s+([^\r\n]+)", body):
            line = candidate.group(1).decode("utf-8", errors="replace")
            explicit = re.search(r"\{#([^}]+)\}\s*$", line)
            if (explicit and explicit.group(1) == fragment) or slug(line) == fragment:
                match = candidate
                heading_line = candidate.group(0)
                break
    if match is None:
        return {"locator_status": "fragment-not-found", "context_digest": None, "context_start": None,
                "context_end": None, "context_unit": None, "heading_digest": None}
    start = max(0, match.start() - 4096)
    end = min(len(body), match.start() + 32768)
    return {"locator_status": "fragment-found", "context_digest": sha(body[start:end]),
            "context_start": start, "context_end": end, "context_unit": "utf8-byte",
            "heading_digest": sha(heading_line) if heading_line else None}


def scan_body_structure(body: bytes, source_id: str) -> list[dict]:
    """Markdown本文を非重複sectionへ分割し、文字列を保存せずoffset/digestだけ返す。"""
    headings = []
    offset = 0
    fence = None
    for line in body.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {b"```", b"~~~"}:
            fence = None if fence == marker else (marker if fence is None else fence)
            offset += len(line)
            continue
        match = None if fence else re.match(rb"^(#{1,6})[ \t]+([^\r\n]+)", line)
        if match:
            raw_heading = match.group(2)
            explicit = re.search(rb"\{#([^}]+)\}\s*$", raw_heading)
            locator = "#" + (explicit.group(1).decode("utf-8", errors="replace") if explicit else slug(raw_heading.decode("utf-8", errors="replace")))
            headings.append((offset, len(match.group(1)), locator, sha(raw_heading)))
        offset += len(line)
    if not headings:
        return [{"section_id": f"section.{source_id}.0000", "locator": "document-root", "level": 0,
                 "section_start": 0, "section_end": len(body), "context_unit": "utf8-byte",
                 "section_digest": sha(body), "heading_digest": None, "classification": "automated-section-unreviewed"}]
    sections = []
    for index, (heading_offset, level, locator, heading_digest) in enumerate(headings):
        start = 0 if index == 0 else heading_offset
        end = headings[index + 1][0] if index + 1 < len(headings) else len(body)
        sections.append({"section_id": f"section.{source_id}.{index:04d}", "locator": locator, "level": level,
                         "section_start": start, "section_end": end, "context_unit": "utf8-byte",
                         "section_digest": sha(body[start:end]), "heading_digest": heading_digest,
                         "classification": "automated-section-unreviewed"})
    return sections


def denominator(source_ids: list[str], needle: tuple[str, ...]) -> dict:
    selected = sorted(source_id for source_id in source_ids if any(value in source_id for value in needle))
    return {"source_ids": selected, "sources": len(selected), "body_exhaustive": 0,
            "human_reviewed_surfaces": 0, "status": "partial"}


def main() -> None:
    sources_doc = yaml.safe_load((ROOT / "sources.lock.yaml").read_text(encoding="utf-8"))
    sources = sources_doc["sources"]
    edges = authority_edges()
    input_digest = sha(canonical({
        "sources": [{"id": item["id"], "url": item["url"], "digest": item["digest"], "version": item["version"]} for item in sources],
        "reference_edges": edges,
    }))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    expected_files = {f"{source['id']}.json" for source in sources}
    for stale in OUTPUT_DIR.glob("*.json"):
        if stale.name not in expected_files:
            stale.unlink()
    artifacts = []
    for index, source in enumerate(sources, start=1):
        request = urllib.request.Request(source["url"], headers={
            "User-Agent": "rabbitmq-reference-atlas-authority-extractor/1.0",
            "Accept": "text/plain,text/markdown,text/html,application/json;q=0.9,*/*;q=0.2",
        })
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                fetched_digest = sha(body)
                matched = fetched_digest == source["digest"]
                fetch = {"status": "matched" if matched else "stale", "fetched_digest": fetched_digest,
                         "locked_digest_match": matched, "http_status": response.status,
                         "final_url": response.geturl(), "content_type": response.headers.get("content-type"),
                         "fetched_bytes": len(body), "error_digest": None}
                locations = [locate(body, edge["locator"]) if matched else {
                    "locator_status": "not-evaluated-stale-body", "context_digest": None,
                    "context_start": None, "context_end": None, "context_unit": None, "heading_digest": None,
                } for edge in edges.get(source["id"], [])]
                body_structure = {"status": "structurally-scanned" if matched else "not-evaluated-stale-body",
                                  "method": "markdown-section-byte-partition-v1", "review_status": "automated-unreviewed",
                                  "body_storage": "digest-and-locator-context-digest-only",
                                  "sections": scan_body_structure(body, source["id"]) if matched else []}
        except Exception as error:  # network/HTTPの文字列は保存せずdigestだけを残す
            fetch = {"status": "failed", "fetched_digest": None, "locked_digest_match": False,
                     "http_status": error.code if isinstance(error, urllib.error.HTTPError) else None,
                     "final_url": None, "content_type": None, "fetched_bytes": None,
                     "error_digest": sha(str(error).encode())}
            locations = [{"locator_status": "not-evaluated-fetch-failed", "context_digest": None,
                          "context_start": None, "context_end": None, "context_unit": None,
                          "heading_digest": None} for _ in edges.get(source["id"], [])]
            body_structure = {"status": "not-evaluated-fetch-failed", "method": "markdown-section-byte-partition-v1",
                              "review_status": "automated-unreviewed", "body_storage": "digest-and-locator-context-digest-only",
                              "sections": []}
        artifact = {
            "schema_version": 1, "source_id": source["id"], "source_url": source["url"],
            "locked_source_digest": source["digest"], "fetch": fetch,
            "extraction": {"method": "locked-body-locator-context-digest", "tool": "rabbitmq-reference-atlas-authority-extractor-v1",
                           "review_status": "automated-unreviewed", "body_storage": "digest-and-locator-context-digest-only"},
            "body_structure": body_structure,
            "candidate_surfaces": [{**edge, **location, "classification": "candidate-included-unreviewed"}
                                   for edge, location in zip(edges.get(source["id"], []), locations)],
        }
        path = OUTPUT_DIR / f"{source['id']}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts.append((artifact, path))
        print(f"extracted {index}/{len(sources)} {source['id']} {fetch['status']}")

    candidates = [candidate for artifact, _ in artifacts for candidate in artifact["candidate_surfaces"]]
    body_sections = [section for artifact, _ in artifacts for section in artifact["body_structure"]["sections"]]
    status = Counter(artifact["fetch"]["status"] for artifact, _ in artifacts)
    locator_status = Counter(candidate["locator_status"] for candidate in candidates)
    source_ids = [source["id"] for source in sources]
    snapshot = {
        "schema_version": 1, "atlas_id": "rabbitmq-reference-atlas", "generated_at": "2026-08-28T00:00:00+09:00",
        "status": "incomplete-human-review-required", "input_digest": input_digest,
        "body_storage": "digest-and-locator-context-digest-only",
        "summary": {
            "locked_sources": len(sources), "fetched_digest_matched": status["matched"],
            "fetched_digest_stale": status["stale"], "fetch_failed": status["failed"],
            "candidate_surfaces": len(candidates), "root_locators": locator_status["root-document"],
            "fragments_found": locator_status["fragment-found"], "fragments_not_found": locator_status["fragment-not-found"],
            "locator_evaluations_deferred": locator_status["not-evaluated-stale-body"] + locator_status["not-evaluated-fetch-failed"],
            "reference_edges_classified": len(candidates), "unclassified_reference_edges": 0,
            "body_structure_sources_scanned": sum(artifact["body_structure"]["status"] == "structurally-scanned" for artifact, _ in artifacts),
            "body_structure_sources_deferred": sum(artifact["body_structure"]["status"] != "structurally-scanned" for artifact, _ in artifacts),
            "body_section_candidates": len(body_sections),
            "authority_text_surfaces_exhaustive": False, "human_reviewed_surfaces": 0, "core_v2_eligible_surfaces": 0,
        },
        "denominators": {
            "protocol": denominator(source_ids, ("protocol", "amqp", "mqtt", "stomp", "stream")),
            "plugin": denominator(source_ids, ("plugins", "mqtt", "stomp", "stream-plugin", "federation", "shovel", "oauth2", "ldap", "prometheus")),
            "operator": denominator(source_ids, ("operator",)),
        },
        "sources": [{"id": artifact["source_id"], "path": path.relative_to(ROOT).as_posix(),
                     "digest": sha(path.read_bytes()), "locked_digest_match": artifact["fetch"]["locked_digest_match"],
                     "candidate_surfaces": len(artifact["candidate_surfaces"]),
                     "body_structure_status": artifact["body_structure"]["status"],
                     "body_sections": len(artifact["body_structure"]["sections"]),
                     "locator_status": dict(sorted(Counter(item["locator_status"] for item in artifact["candidate_surfaces"]).items()))}
                    for artifact, path in artifacts],
    }
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Authority locator snapshot: matched={status['matched']} stale={status['stale']} failed={status['failed']} candidates={len(candidates)} sections={len(body_sections)} human_reviewed=0 exhaustive=false")


if __name__ == "__main__":
    main()
