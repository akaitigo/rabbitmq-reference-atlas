#!/usr/bin/env python3
"""Unique Authority documentからcopyright-safe raw anchor候補を列挙する。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter

from authority_body_inventory import (
    DECISIONS_PATH,
    GENERATED_AT,
    INVENTORY_DIRECTORY,
    INVENTORY_INDEX,
    ROOT,
    SELECTOR_CONTRACT,
    anchor_counts,
    artifact_digest,
    collect_inputs,
    extract_raw_anchors,
    sha,
)


def main() -> None:
    inputs = collect_inputs()
    INVENTORY_DIRECTORY.mkdir(parents=True, exist_ok=True)
    expected_files = {f"{item['document_id']}.json" for item in inputs["documents"]}
    unexpected = {path.name for path in INVENTORY_DIRECTORY.glob("*.json")} - expected_files
    if unexpected:
        raise SystemExit(f"未宣言のbody inventory artifactがあります（自動削除しません）: {sorted(unexpected)}")
    artifacts = []
    for position, item in enumerate(inputs["documents"], start=1):
        request = urllib.request.Request(item["fetch_url"], headers={
            "User-Agent": "rabbitmq-reference-atlas-authority-body-inventory/1.0",
            "Accept": "text/plain,text/markdown,text/html,application/xhtml+xml;q=0.9,*/*;q=0.2",
        })
        anchors = []
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                if not body:
                    raise ValueError("empty response")
                fetched_digest = sha(body)
                matched = fetched_digest == item["locked_digest"]
                fetch = {
                    "status": "matched" if matched else "stale", "fetched_digest": fetched_digest,
                    "locked_digest_match": matched, "http_status": response.status,
                    "final_url": response.geturl(), "content_type": response.headers.get("content-type"),
                    "fetched_bytes": len(body), "error_digest": None,
                }
                if matched:
                    anchors = extract_raw_anchors(body, fetched_digest, item["document_id"])
        except Exception as error:
            fetch = {
                "status": "failed", "fetched_digest": None, "locked_digest_match": False,
                "http_status": error.code if isinstance(error, urllib.error.HTTPError) else None,
                "final_url": None, "content_type": None, "fetched_bytes": None,
                "error_digest": sha(str(error).encode()),
            }
        artifact = {
            "schema_version": 1, "document_id": item["document_id"], "fetch_url": item["fetch_url"],
            "source_ids": item["source_ids"], "locked_body_digest": item["locked_digest"], "fetch": fetch,
            "extraction": {
                "method": "fixed-selector-raw-anchor-v1", "tool": "rabbitmq-reference-atlas-authority-body-inventory-v1",
                "tool_digest": inputs["tool_digest"], "selector_contract": SELECTOR_CONTRACT,
                "selector_exhaustive_for_locked_body": fetch["status"] == "matched",
                "authority_semantics_exhaustive": False, "review_status": "automated-unreviewed",
                "body_storage": "digest-locator-and-offset-only",
            },
            "anchors": anchors,
        }
        path = INVENTORY_DIRECTORY / f"{item['document_id']}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts.append(artifact)
        print(f"inventoried {position}/{len(inputs['documents'])} {item['document_id']} {fetch['status']} anchors={len(anchors)}")

    artifacts.sort(key=lambda artifact: artifact["document_id"])
    all_anchors = [anchor for artifact in artifacts for anchor in artifact["anchors"]]
    selector_counts = Counter(anchor["selector"] for anchor in all_anchors)
    status_counts = Counter(artifact["fetch"]["status"] for artifact in artifacts)
    index = {
        "schema_version": 1, "atlas_id": "rabbitmq-reference-atlas", "generated_at": GENERATED_AT,
        "status": "incomplete-human-review-required", "input_digest": inputs["input_digest"],
        "tool_digest": inputs["tool_digest"], "body_storage": "digest-locator-and-offset-only",
        "selector_contract": SELECTOR_CONTRACT,
        "semantic_accounting": {
            "raw_anchors_count_toward_surface_inventory": False,
            "raw_anchors_count_toward_depth": False,
            "promotion_requires": "recorded-human-decision",
            "decisions_path": DECISIONS_PATH.relative_to(ROOT).as_posix(),
        },
        "summary": {
            "source_entries": inputs["source_entries"], "unique_documents": len(inputs["documents"]),
            "matched_documents": status_counts["matched"], "stale_documents": status_counts["stale"],
            "failed_documents": status_counts["failed"], "selector_exhaustive_documents": status_counts["matched"],
            "raw_anchors": len(all_anchors), "raw_anchors_by_selector": dict(sorted(selector_counts.items())),
            "pending_human_anchors": len(all_anchors), "human_reviewed_anchors": 0,
            "promoted_surface_ids": 0, "promoted_behavior_ids": 0,
            "core_v2_eligible_artifacts": 0, "authority_semantics_exhaustive": False,
        },
        "documents": [{
            "id": artifact["document_id"],
            "path": (INVENTORY_DIRECTORY / f"{artifact['document_id']}.json").relative_to(ROOT).as_posix(),
            "digest": artifact_digest(artifact), "fetch_status": artifact["fetch"]["status"],
            "source_entries": len(artifact["source_ids"]), "raw_anchors": len(artifact["anchors"]),
            "raw_anchors_by_selector": anchor_counts(artifact["anchors"]),
        } for artifact in artifacts],
    }
    INVENTORY_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Authority body inventory: matched={status_counts['matched']}/{len(artifacts)} stale={status_counts['stale']} failed={status_counts['failed']} raw_anchors={len(all_anchors)} pending_human={len(all_anchors)} semantic_promotions=0")


if __name__ == "__main__":
    main()
