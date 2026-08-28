#!/usr/bin/env python3
"""Authority raw anchor inventoryと専用非後退baselineをoffline検証する。"""

from __future__ import annotations

import json
from collections import Counter

from authority_body_inventory import (
    ANCHOR_KEYS,
    BASELINE_ID,
    BASELINE_PATH,
    DECISIONS_PATH,
    GENERATED_AT,
    INVENTORY_DIRECTORY,
    INVENTORY_INDEX,
    MIGRATION_PATH,
    REPORT_PATH,
    ROOT,
    SELECTOR_CONTRACT,
    SHA,
    anchor_counts,
    artifact_digest,
    collect_inputs,
    exact,
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_anchor(anchor: dict, label: str) -> None:
    exact(anchor, ANCHOR_KEYS, label)
    if not re_anchor(anchor["id"]) or anchor["selector"] not in SELECTOR_CONTRACT:
        raise ValueError(f"{label}: stable IDまたはselectorが不正です")
    if anchor["context_start"] < 0 or anchor["context_end"] <= anchor["context_start"]:
        raise ValueError(f"{label}: context offsetが不正です")
    if anchor["context_unit"] != "utf8-byte" or not SHA.match(anchor["context_digest"]):
        raise ValueError(f"{label}: context unit/digestが不正です")
    if anchor["label_digest"] is not None and not SHA.match(anchor["label_digest"]):
        raise ValueError(f"{label}: label digestが不正です")
    if anchor["locator_kind"] not in {"document-root", "fragment", "locked-body-offset"}:
        raise ValueError(f"{label}: locator kindが不正です")
    if len(anchor["surface_ids"]) != len(set(anchor["surface_ids"])) or len(anchor["behavior_ids"]) != len(set(anchor["behavior_ids"])):
        raise ValueError(f"{label}: promotion IDが重複しています")


def re_anchor(value: str) -> bool:
    return isinstance(value, str) and value.startswith("anchor-") and len(value) == 27 and all(char in "0123456789abcdef" for char in value[7:])


def main() -> None:
    inputs = collect_inputs()
    index = read(INVENTORY_INDEX)
    decisions = read(DECISIONS_PATH)
    exact(index, {
        "schema_version", "atlas_id", "generated_at", "status", "input_digest", "tool_digest",
        "body_storage", "selector_contract", "semantic_accounting", "summary", "documents",
    }, "Authority body inventory index")
    exact(index["semantic_accounting"], {
        "raw_anchors_count_toward_surface_inventory", "raw_anchors_count_toward_depth",
        "promotion_requires", "decisions_path",
    }, "Authority body semantic accounting")
    exact(index["summary"], {
        "source_entries", "unique_documents", "matched_documents", "stale_documents", "failed_documents",
        "selector_exhaustive_documents", "raw_anchors", "raw_anchors_by_selector", "pending_human_anchors",
        "human_reviewed_anchors", "promoted_surface_ids", "promoted_behavior_ids",
        "core_v2_eligible_artifacts", "authority_semantics_exhaustive",
    }, "Authority body inventory summary")
    if index["schema_version"] != 1 or index["atlas_id"] != "rabbitmq-reference-atlas" or index["generated_at"] != GENERATED_AT:
        raise SystemExit("Authority body inventory identity mismatch")
    if index["status"] != "incomplete-human-review-required" or index["body_storage"] != "digest-locator-and-offset-only":
        raise SystemExit("Authority body inventory status/storage mismatch")
    if index["input_digest"] != inputs["input_digest"] or index["tool_digest"] != inputs["tool_digest"]:
        raise SystemExit("Authority body inventory input/tool digest drift")
    if index["selector_contract"] != SELECTOR_CONTRACT:
        raise SystemExit("Authority body selector contract drift")
    if index["semantic_accounting"] != {
        "raw_anchors_count_toward_surface_inventory": False,
        "raw_anchors_count_toward_depth": False,
        "promotion_requires": "recorded-human-decision",
        "decisions_path": DECISIONS_PATH.relative_to(ROOT).as_posix(),
    }:
        raise SystemExit("Raw anchorをSemantic Surface/Depthへ算入できません")
    exact(decisions, {"schema_version", "inventory_id", "policy", "decisions"}, "Authority body review decisions")
    if decisions["schema_version"] != 1 or decisions["inventory_id"] != "rabbitmq-authority-body-inventory-v1" or decisions["policy"] != "human-recorded-decisions-only":
        raise SystemExit("Authority body human decision policy mismatch")
    decisions_by_anchor = {}
    for decision in decisions["decisions"]:
        exact(decision, {
            "id", "document_id", "anchor_id", "outcome", "surface_ids", "behavior_ids",
            "reviewer", "reviewed_at", "rationale_digest",
        }, f"Authority human decision {decision.get('id')}")
        if decision["anchor_id"] in decisions_by_anchor or decision["outcome"] not in {"promote", "reject"}:
            raise SystemExit(f"Authority human decision duplicate/outcome mismatch: {decision['anchor_id']}")
        if not SHA.match(decision["rationale_digest"]):
            raise SystemExit(f"Authority human decision rationale digest mismatch: {decision['anchor_id']}")
        if decision["outcome"] == "promote" and (not decision["surface_ids"] or not decision["behavior_ids"]):
            raise SystemExit(f"PromotionはSurface/behavior両方を必要とします: {decision['anchor_id']}")
        if decision["outcome"] == "reject" and (decision["surface_ids"] or decision["behavior_ids"]):
            raise SystemExit(f"Rejected anchorをSurfaceへ接続できません: {decision['anchor_id']}")
        decisions_by_anchor[decision["anchor_id"]] = decision

    expected_files = {f"{item['document_id']}.json" for item in inputs["documents"]}
    actual_files = {path.name for path in INVENTORY_DIRECTORY.glob("*.json")}
    if actual_files != expected_files:
        raise SystemExit("Authority body artifact集合がunique document集合と一致しません")
    index_records = {record["id"]: record for record in index["documents"]}
    if len(index_records) != len(index["documents"]) or set(index_records) != {item["document_id"] for item in inputs["documents"]}:
        raise SystemExit("Authority body index document集合に欠落または重複があります")
    counters = Counter()
    selector_counts = Counter()
    all_anchor_ids: set[str] = set()
    pending = reviewed = promoted_surfaces = promoted_behaviors = 0
    artifacts_by_document = {}
    for item in inputs["documents"]:
        path = INVENTORY_DIRECTORY / f"{item['document_id']}.json"
        artifact = read(path)
        artifacts_by_document[item["document_id"]] = artifact
        exact(artifact, {"schema_version", "document_id", "fetch_url", "source_ids", "locked_body_digest", "fetch", "extraction", "anchors"}, path.name)
        exact(artifact["fetch"], {"status", "fetched_digest", "locked_digest_match", "http_status", "final_url", "content_type", "fetched_bytes", "error_digest"}, f"{path.name} fetch")
        exact(artifact["extraction"], {"method", "tool", "tool_digest", "selector_contract", "selector_exhaustive_for_locked_body", "authority_semantics_exhaustive", "review_status", "body_storage"}, f"{path.name} extraction")
        if artifact["schema_version"] != 1 or artifact["document_id"] != item["document_id"] or artifact["fetch_url"] != item["fetch_url"]:
            raise SystemExit(f"Authority body document identity mismatch: {path.name}")
        if artifact["source_ids"] != item["source_ids"] or artifact["locked_body_digest"] != item["locked_digest"]:
            raise SystemExit(f"Authority body Source lock identity mismatch: {path.name}")
        if artifact["extraction"] != {
            "method": "fixed-selector-raw-anchor-v1", "tool": "rabbitmq-reference-atlas-authority-body-inventory-v1",
            "tool_digest": inputs["tool_digest"], "selector_contract": SELECTOR_CONTRACT,
            "selector_exhaustive_for_locked_body": artifact["fetch"]["status"] == "matched",
            "authority_semantics_exhaustive": False, "review_status": "automated-unreviewed",
            "body_storage": "digest-locator-and-offset-only",
        }:
            raise SystemExit(f"Authority body extraction boundary mismatch: {path.name}")
        status = artifact["fetch"]["status"]
        counters[status] += 1
        if status == "matched":
            if not artifact["fetch"]["locked_digest_match"] or artifact["fetch"]["fetched_digest"] != item["locked_digest"] or not artifact["anchors"]:
                raise SystemExit(f"Matched Authority body boundary mismatch: {path.name}")
        elif status == "stale":
            if artifact["fetch"]["locked_digest_match"] or artifact["fetch"]["fetched_digest"] in {None, item["locked_digest"]} or artifact["anchors"]:
                raise SystemExit(f"Stale Authority body boundary mismatch: {path.name}")
        elif status == "failed":
            if artifact["fetch"]["fetched_digest"] is not None or not SHA.match(artifact["fetch"]["error_digest"] or "") or artifact["anchors"]:
                raise SystemExit(f"Failed Authority body boundary mismatch: {path.name}")
        else:
            raise SystemExit(f"Unknown Authority fetch status: {path.name}")
        local_ids: set[str] = set()
        for position, anchor in enumerate(artifact["anchors"]):
            validate_anchor(anchor, f"{path.name} anchor {position}")
            if anchor["id"] in local_ids or anchor["id"] in all_anchor_ids:
                raise SystemExit(f"Authority raw anchor ID duplicated: {anchor['id']}")
            local_ids.add(anchor["id"])
            all_anchor_ids.add(anchor["id"])
            if position == 0 and (anchor["selector"] != "document-root" or anchor["parent_anchor_id"] is not None):
                raise SystemExit(f"Authority document root mismatch: {path.name}")
            if position > 0 and anchor["parent_anchor_id"] not in local_ids:
                raise SystemExit(f"Authority raw anchor parent must precede child: {anchor['id']}")
            decision = decisions_by_anchor.get(anchor["id"])
            if anchor["classification_status"] == "pending-human":
                pending += 1
                if decision is not None or anchor["decision_id"] is not None or anchor["surface_ids"] or anchor["behavior_ids"]:
                    raise SystemExit(f"Pending raw anchorをSemantic Surfaceへ接続できません: {anchor['id']}")
            elif anchor["classification_status"] == "human-reviewed-promoted":
                reviewed += 1
                if not decision or decision["outcome"] != "promote" or anchor["decision_id"] != decision["id"] or anchor["surface_ids"] != decision["surface_ids"] or anchor["behavior_ids"] != decision["behavior_ids"]:
                    raise SystemExit(f"Human promotion decision mismatch: {anchor['id']}")
                promoted_surfaces += len(anchor["surface_ids"])
                promoted_behaviors += len(anchor["behavior_ids"])
            elif anchor["classification_status"] == "human-reviewed-rejected":
                reviewed += 1
                if not decision or decision["outcome"] != "reject" or anchor["decision_id"] != decision["id"] or anchor["surface_ids"] or anchor["behavior_ids"]:
                    raise SystemExit(f"Human rejection decision mismatch: {anchor['id']}")
            else:
                raise SystemExit(f"Unknown Authority classification status: {anchor['id']}")
            selector_counts[anchor["selector"]] += 1
        record = index_records[item["document_id"]]
        exact(record, {"id", "path", "digest", "fetch_status", "source_entries", "raw_anchors", "raw_anchors_by_selector"}, f"Authority body index {item['document_id']}")
        expected_record = {
            "id": item["document_id"], "path": path.relative_to(ROOT).as_posix(),
            "digest": artifact_digest(artifact), "fetch_status": status,
            "source_entries": len(item["source_ids"]), "raw_anchors": len(artifact["anchors"]),
            "raw_anchors_by_selector": anchor_counts(artifact["anchors"]),
        }
        if record != expected_record:
            raise SystemExit(f"Authority body index record mismatch: {item['document_id']}")
    if set(decisions_by_anchor) - all_anchor_ids:
        raise SystemExit(f"Human decisionが未知anchorを参照します: {sorted(set(decisions_by_anchor) - all_anchor_ids)}")
    expected_summary = {
        "source_entries": inputs["source_entries"], "unique_documents": len(inputs["documents"]),
        "matched_documents": counters["matched"], "stale_documents": counters["stale"],
        "failed_documents": counters["failed"], "selector_exhaustive_documents": counters["matched"],
        "raw_anchors": len(all_anchor_ids), "raw_anchors_by_selector": dict(sorted(selector_counts.items())),
        "pending_human_anchors": pending, "human_reviewed_anchors": reviewed,
        "promoted_surface_ids": promoted_surfaces, "promoted_behavior_ids": promoted_behaviors,
        "core_v2_eligible_artifacts": 0, "authority_semantics_exhaustive": False,
    }
    if index["summary"] != expected_summary:
        raise SystemExit("Authority body inventory summary mismatch")

    baseline = read(BASELINE_PATH)
    migration = read(MIGRATION_PATH)
    exact(baseline, {"schema_version", "id", "captured_at", "input_digest", "tool_digest", "source_entries", "unique_documents", "selector_contract", "documents"}, "Authority body baseline")
    exact(migration, {"schema_version", "baseline_id", "replacements"}, "Authority body migration")
    if baseline["schema_version"] != 1 or baseline["id"] != BASELINE_ID or baseline["captured_at"] != GENERATED_AT:
        raise SystemExit("Authority body baseline identity mismatch")
    if not SHA.match(baseline["input_digest"]) or not SHA.match(baseline["tool_digest"]):
        raise SystemExit("Authority body baseline input/tool digest mismatch")
    if migration["schema_version"] != 1 or migration["baseline_id"] != BASELINE_ID:
        raise SystemExit("Authority body migration identity mismatch")
    if index["summary"]["source_entries"] < baseline["source_entries"] or index["summary"]["unique_documents"] < baseline["unique_documents"]:
        raise SystemExit("Authority body Source/document floor regression")
    if index["selector_contract"] != baseline["selector_contract"]:
        raise SystemExit("Authority body selector contract regression")
    baseline_anchor_ids = {anchor_id for document in baseline["documents"] for anchor_id in document["anchor_ids"]}
    if len(baseline_anchor_ids) != sum(len(document["anchor_ids"]) for document in baseline["documents"]):
        raise SystemExit("Authority body baseline anchor ID duplicated")
    replacements = {}
    replacement_new_ids = set()
    for replacement in migration["replacements"]:
        exact(replacement, {"old_anchor_id", "new_anchor_ids", "execution_proof", "migration_evidence", "reason"}, f"Authority body replacement {replacement.get('old_anchor_id')}")
        old_id = replacement["old_anchor_id"]
        if old_id not in baseline_anchor_ids or old_id in replacements or not replacement["new_anchor_ids"] or len(replacement["reason"]) < 20:
            raise SystemExit(f"Authority body replacement mapping mismatch: {old_id}")
        if replacement["execution_proof"] == replacement["migration_evidence"]:
            raise SystemExit(f"Authority body replacement requires distinct proof/evidence: {old_id}")
        for proof in (replacement["execution_proof"], replacement["migration_evidence"]):
            if not (ROOT / proof).is_file():
                raise SystemExit(f"Authority body replacement Evidence missing: {proof}")
        for new_id in replacement["new_anchor_ids"]:
            if new_id not in all_anchor_ids or new_id in replacement_new_ids:
                raise SystemExit(f"Authority body replacement target mismatch/shared: {new_id}")
            replacement_new_ids.add(new_id)
        replacements[old_id] = replacement
    baseline_documents = {document["id"]: document for document in baseline["documents"]}
    if len(baseline_documents) != len(baseline["documents"]):
        raise SystemExit("Authority body baseline document ID duplicated")
    retained = replaced = 0
    for expected in baseline["documents"]:
        exact(expected, {"id", "path", "locked_body_digest", "source_ids", "anchor_ids"}, f"Authority body baseline document {expected['id']}")
        current = artifacts_by_document.get(expected["id"])
        if (not current or expected["path"] != index_records[expected["id"]]["path"]
                or current["locked_body_digest"] != expected["locked_body_digest"]
                or current["source_ids"] != expected["source_ids"]):
            raise SystemExit(f"Authority body baseline document removed/replaced: {expected['id']}")
        if expected["anchor_ids"] != sorted(expected["anchor_ids"]):
            raise SystemExit(f"Authority body baseline anchor order mismatch: {expected['id']}")
        for anchor_id in expected["anchor_ids"]:
            if anchor_id in all_anchor_ids:
                retained += 1
            elif anchor_id in replacements:
                replaced += 1
            else:
                raise SystemExit(f"Authority raw anchor removed without migration: {anchor_id}")
    if any(old_id in all_anchor_ids for old_id in replacements):
        raise SystemExit("現存anchorをreplacement扱いにできません")
    report = {
        "schema_version": 1, "baseline_id": BASELINE_ID,
        "baseline_documents": len(baseline["documents"]), "current_documents": len(artifacts_by_document),
        "baseline_raw_anchors": len(baseline_anchor_ids), "current_raw_anchors": len(all_anchor_ids),
        "retained": retained, "replaced": replaced,
        "added": len(all_anchor_ids) - retained - len(replacement_new_ids),
        "pending_human": pending, "human_reviewed": reviewed,
        "semantic_surface_counted_from_raw_anchors": 0,
        "depth_counted_from_raw_anchors": 0, "status": "pass",
        "baseline_tool_digest": baseline["tool_digest"], "current_tool_digest": index["tool_digest"],
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"authority-body PASS: documents={len(artifacts_by_document)} matched={counters['matched']} stale={counters['stale']} failed={counters['failed']} raw_anchors={len(all_anchor_ids)} pending_human={pending} semantic_counted=0 retained={retained}/{len(baseline_anchor_ids)}")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
