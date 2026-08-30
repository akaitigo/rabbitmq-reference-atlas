#!/usr/bin/env python3
"""Authority raw anchorを人手一次資料Review queueへ完全投影する契約。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BODY_INDEX_PATH = ROOT / "authority/body-inventory.snapshot.json"
BODY_DIRECTORY = ROOT / "authority/body-inventory-draft"
LOCATOR_DIRECTORY = ROOT / "authority/locator-drafts"
QUEUE_INDEX_PATH = ROOT / "authority/review-queue.snapshot.json"
QUEUE_DIRECTORY = ROOT / "authority/review-queue-draft"
DECISION_LEDGER_PATH = ROOT / "authority/reviews/decisions.json"
PROMOTION_BASELINE_PATH = ROOT / "baseline/authority-review-prequeue-v1.json"
GENERATED_AT = "2026-08-28T00:00:00+09:00"
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
QUEUE_ITEM_KEYS = {
    "anchor_id", "document_id", "document_url", "source_ids", "locked_source_digest",
    "inventory_tool_digest", "review_queue_tool_digest", "locator", "locator_kind", "selector",
    "selector_kind", "tag", "heading_level", "parent_anchor_id", "context_start", "context_end",
    "context_unit", "context_digest", "label_digest", "existing_reference_edge_ids",
    "proposed_priority", "priority_proposal_reasons", "proposed_candidate_cluster_id",
    "proposed_batch_id", "state",
}
BINDING_KEYS = {
    "anchor_id", "document_id", "document_url", "locked_source_digest", "inventory_tool_digest",
    "review_queue_tool_digest", "locator", "locator_kind", "context_start", "context_end",
    "context_unit", "context_digest", "label_digest",
}


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def short_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def exact(value: dict, keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ValueError(f"{label}: unknown/body fieldまたは必須field欠落: {sorted(set(value) ^ keys)}")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_digest(value: dict) -> str:
    return sha((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def tool_digest() -> str:
    files = [
        "scripts/authority_review_queue.py",
        "scripts/generate-authority-review-queue.py",
        "scripts/validate-authority-review-queue.py",
        "scripts/test-authority-review-queue.py",
    ]
    return sha(b"\0".join(path.encode() + b"\0" + (ROOT / path).read_bytes() for path in files))


def proposed_priority(anchor: dict, edge_ids: list[str]) -> tuple[int, list[str]]:
    if edge_ids:
        return 0, ["existing-domain-reference-locator-match"]
    if anchor["selector_kind"] in {"markdown-atx-heading", "html-element"} and anchor["tag"] in {
        "h1", "h2", "h3", "h4", "h5", "h6", "dfn",
    }:
        return 1, ["label-bearing-anchor"]
    return 2, ["structural-or-document-anchor"]


def batch_proposal_id(priority: int, selector_kind: str, anchor_id: str) -> str:
    bucket = format(int(short_hash(anchor_id, 2), 16) % 64, "02x")
    kind = re.sub(r"[^a-z0-9-]", "-", selector_kind.lower()).strip("-")
    return f"review-p{priority}-{kind}-{bucket}"


def source_binding(item: dict) -> dict:
    return {
        "anchor_id": item["anchor_id"], "document_id": item["document_id"],
        "document_url": item["document_url"], "locked_source_digest": item["locked_source_digest"],
        "inventory_tool_digest": item["inventory_tool_digest"],
        "review_queue_tool_digest": item["review_queue_tool_digest"], "locator": item["locator"],
        "locator_kind": item["locator_kind"], "context_start": item["context_start"],
        "context_end": item["context_end"], "context_unit": item["context_unit"],
        "context_digest": item["context_digest"], "label_digest": item["label_digest"],
    }


def result_catalog() -> dict[str, set[str]]:
    authority_surface_ids = set()
    for path in sorted((ROOT / "surface/authority").glob("*.authority-surfaces.yaml")):
        authority_surface_ids.update(item["id"] for item in yaml.safe_load(path.read_text(encoding="utf-8"))["surfaces"])
    inventory = yaml.safe_load((ROOT / "surface.inventory.yaml").read_text(encoding="utf-8"))
    return {
        "authority-surface": authority_surface_ids,
        "atomic-behavior": {item["behavior_id"] for item in inventory["items"]},
    }


def validate_promotion_floor(
    catalog: dict[str, set[str]], baseline_catalog: dict[str, set[str]], decision_results: set[tuple[str, str]],
) -> None:
    if any(not baseline_catalog[kind].issubset(catalog[kind]) for kind in baseline_catalog):
        raise ValueError("Queue導入前Authority Surface/Behaviorが削除されています")
    for kind in catalog:
        for item_id in catalog[kind] - baseline_catalog[kind]:
            if (kind, item_id) not in decision_results:
                raise ValueError(f"Human primary-source decisionなしのSurface/Behavior昇格です: {kind}:{item_id}")


def validate_decisions(decisions: list[dict], item_by_id: dict[str, dict], catalog: dict[str, set[str]] | None = None) -> set[str]:
    decision_ids: set[str] = set()
    decided_anchors: set[str] = set()
    new_item_owner: dict[str, str] = {}
    for decision in decisions:
        exact(decision, {
            "decision_id", "action", "anchor_ids", "source_bindings", "reason", "reason_digest",
            "reviewer", "reviewed_at", "review_method", "mapping", "result_items",
        }, f"Decision {decision.get('decision_id')}")
        decision_id = decision["decision_id"]
        if not isinstance(decision_id, str) or not re.match(r"^decision\.[a-z0-9.-]+$", decision_id) or decision_id in decision_ids:
            raise ValueError(f"Decision IDが不正または重複しています: {decision_id}")
        decision_ids.add(decision_id)
        if decision["action"] not in {"include", "exclude", "merge", "split"}:
            raise ValueError(f"Decision actionが不正です: {decision_id}")
        if decision["review_method"] != "manual-primary-source":
            raise ValueError(f"人の一次資料確認が必要です: {decision_id}")
        if not isinstance(decision["reason"], str) or len(decision["reason"].strip()) < 40:
            raise ValueError(f"Decision reasonが不足しています: {decision_id}")
        if decision["reason_digest"] != sha(decision["reason"].encode()):
            raise ValueError(f"Decision reason digestが一致しません: {decision_id}")
        reviewer = decision["reviewer"].strip() if isinstance(decision["reviewer"], str) else ""
        if len(reviewer) < 2 or re.match(r"^(?:auto(?:mated)?|agent|bot|system|machine)(?:$|[-_. ])", reviewer, re.I):
            raise ValueError(f"人手reviewer provenanceが不足しています: {decision_id}")
        if not isinstance(decision["reviewed_at"], str) or not re.match(r"^\d{4}-\d{2}-\d{2}T", decision["reviewed_at"]):
            raise ValueError(f"reviewed_atがISO date-timeではありません: {decision_id}")
        try:
            reviewed_at = datetime.fromisoformat(decision["reviewed_at"])
        except ValueError as error:
            raise ValueError(f"reviewed_atがISO date-timeではありません: {decision_id}") from error
        if reviewed_at.tzinfo is None:
            raise ValueError(f"reviewed_atにtimezoneがありません: {decision_id}")
        anchor_ids = decision["anchor_ids"]
        if (not anchor_ids or len(anchor_ids) != len(set(anchor_ids))
                or len(decision["source_bindings"]) != len(anchor_ids)
                or len(decision["mapping"]) != len(anchor_ids)):
            raise ValueError(f"Decision anchor/binding/mapping cardinalityが不正です: {decision_id}")
        for anchor_id in anchor_ids:
            if anchor_id in decided_anchors:
                raise ValueError(f"Anchorに複数decisionがあります: {anchor_id}")
            if anchor_id not in item_by_id:
                raise ValueError(f"Queue外またはstale hold中anchorのdecisionです: {anchor_id}")
            decided_anchors.add(anchor_id)
        binding_by_id = {binding.get("anchor_id"): binding for binding in decision["source_bindings"]}
        mapping_by_id = {mapping.get("old_anchor_id"): mapping for mapping in decision["mapping"]}
        if len(binding_by_id) != len(anchor_ids) or len(mapping_by_id) != len(anchor_ids):
            raise ValueError(f"Decision binding/mapping IDが重複しています: {decision_id}")
        for anchor_id in anchor_ids:
            binding = binding_by_id.get(anchor_id)
            if binding is not None:
                exact(binding, BINDING_KEYS, f"Decision binding {anchor_id}")
            if binding != source_binding(item_by_id[anchor_id]):
                raise ValueError(f"Decision digest/locator bindingがQueueと一致しません: {anchor_id}")
            mapping = mapping_by_id.get(anchor_id)
            if mapping is not None:
                exact(mapping, {"old_anchor_id", "new_item_ids"}, f"Decision mapping {anchor_id}")
            if (mapping is None or len(mapping["new_item_ids"]) != len(set(mapping["new_item_ids"]))
                    or any(not re.match(r"^[a-z][a-z0-9.-]+$", item_id) for item_id in mapping["new_item_ids"])):
                raise ValueError(f"Decision mappingが不正です: {anchor_id}")
        result_ids = []
        for result in decision["result_items"]:
            exact(result, {"id", "item_type"}, f"Decision result {decision_id}")
            if (not re.match(r"^[a-z][a-z0-9.-]+$", result["id"])
                    or result["item_type"] not in {"authority-surface", "atomic-behavior"}):
                raise ValueError(f"Decision result itemが不正です: {decision_id}")
            if catalog is not None and result["id"] not in catalog[result["item_type"]]:
                raise ValueError(f"Decision resultがAuthority/Behavior正本に存在しません: {decision_id}:{result['id']}")
            result_ids.append(result["id"])
        if len(result_ids) != len(set(result_ids)):
            raise ValueError(f"Decision result itemが重複しています: {decision_id}")
        mapped_ids = sorted(set(item_id for mapping in decision["mapping"] for item_id in mapping["new_item_ids"]))
        if mapped_ids != sorted(result_ids):
            raise ValueError(f"Decision mappingとProtocol/Behavior resultが一致しません: {decision_id}")
        mappings = decision["mapping"]
        if decision["action"] == "exclude" and any(mapping["new_item_ids"] for mapping in mappings):
            raise ValueError(f"excludeはresultへmappingできません: {decision_id}")
        if decision["action"] == "include" and any(not mapping["new_item_ids"] for mapping in mappings):
            raise ValueError(f"includeには旧→新mappingが必要です: {decision_id}")
        flat_ids = [item_id for mapping in mappings for item_id in mapping["new_item_ids"]]
        if decision["action"] == "include" and len(flat_ids) != len(set(flat_ids)):
            raise ValueError(f"includeのID共有にはmerge decisionが必要です: {decision_id}")
        mapping_sets = {tuple(sorted(mapping["new_item_ids"])) for mapping in mappings}
        if decision["action"] == "merge" and (len(anchor_ids) < 2 or any(not mapping["new_item_ids"] for mapping in mappings) or len(mapping_sets) != 1):
            raise ValueError(f"merge mappingが不正です: {decision_id}")
        if decision["action"] == "split" and (len(anchor_ids) != 1 or len(mappings[0]["new_item_ids"]) < 2):
            raise ValueError(f"split mappingが不正です: {decision_id}")
        for new_id in set(flat_ids):
            owner = new_item_owner.get(new_id)
            if owner is not None and owner != decision_id:
                raise ValueError(f"result IDが複数decisionで共有されています: {new_id}")
            new_item_owner[new_id] = decision_id
    return decided_anchors


def build_queue() -> tuple[dict, list[dict], dict]:
    body_index = read(BODY_INDEX_PATH)
    queue_tool_digest = tool_digest()
    artifacts = [read(ROOT / record["path"]) for record in body_index["documents"]]
    anchor_ids = sorted(anchor["id"] for artifact in artifacts for anchor in artifact["anchors"])
    queue_id = f"authority-review-{short_hash(body_index['input_digest'] + chr(0) + chr(0).join(anchor_ids), 20)}"
    input_digest = sha(json.dumps({
        "body_input_digest": body_index["input_digest"], "anchor_ids": anchor_ids,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())

    edge_ids_by_source_locator: dict[tuple[str, str], list[str]] = {}
    for source_id in sorted(set(source_id for artifact in artifacts for source_id in artifact["source_ids"])):
        extracted = read(LOCATOR_DIRECTORY / f"{source_id}.json")
        for edge in extracted["candidate_surfaces"]:
            edge_ids_by_source_locator.setdefault((source_id, edge["locator"]), []).append(edge["edge_id"])
    label_groups: dict[tuple[str, str], list[str]] = {}
    for artifact in artifacts:
        for anchor in artifact["anchors"]:
            if anchor["label_digest"] and anchor["tag"] in {"h1", "h2", "h3", "h4", "h5", "h6", "dfn"}:
                label_groups.setdefault((anchor["selector_kind"], anchor["label_digest"]), []).append(anchor["id"])
    cluster_by_anchor = {}
    for key, ids in label_groups.items():
        if len(ids) > 1:
            cluster_id = f"candidate-cluster-{short_hash(key[0] + chr(0) + key[1], 20)}"
            for anchor_id in ids:
                cluster_by_anchor[anchor_id] = cluster_id

    grouped: dict[str, list[dict]] = {}
    for artifact in artifacts:
        if artifact["fetch"]["status"] != "matched":
            continue
        for anchor in artifact["anchors"]:
            edge_ids = sorted(set(
                edge_id for source_id in artifact["source_ids"]
                for edge_id in edge_ids_by_source_locator.get((source_id, anchor["locator"]), [])
            ))
            priority, reasons = proposed_priority(anchor, edge_ids)
            batch_id = batch_proposal_id(priority, anchor["selector_kind"], anchor["id"])
            item = {
                "anchor_id": anchor["id"], "document_id": artifact["document_id"],
                "document_url": artifact["fetch_url"], "source_ids": artifact["source_ids"],
                "locked_source_digest": artifact["locked_body_digest"],
                "inventory_tool_digest": artifact["extraction"]["tool_digest"],
                "review_queue_tool_digest": queue_tool_digest, "locator": anchor["locator"],
                "locator_kind": anchor["locator_kind"], "selector": anchor["selector"],
                "selector_kind": anchor["selector_kind"], "tag": anchor["tag"],
                "heading_level": anchor["heading_level"], "parent_anchor_id": anchor["parent_anchor_id"],
                "context_start": anchor["context_start"], "context_end": anchor["context_end"],
                "context_unit": anchor["context_unit"], "context_digest": anchor["context_digest"],
                "label_digest": anchor["label_digest"], "existing_reference_edge_ids": edge_ids,
                "proposed_priority": priority, "priority_proposal_reasons": reasons,
                "proposed_candidate_cluster_id": cluster_by_anchor.get(anchor["id"]),
                "proposed_batch_id": batch_id, "state": "pending-human",
            }
            grouped.setdefault(batch_id, []).append(item)
    batches = []
    for batch_id, items in sorted(grouped.items()):
        batches.append({
            "schema_version": 1, "queue_id": queue_id, "batch_id": batch_id,
            "status": "pending-human", "machine_assistance": "priority-cluster-batch-proposals-only",
            "semantic_decisions": "none", "items": sorted(items, key=lambda item: item["anchor_id"]),
        })
    all_items = [item for batch in batches for item in batch["items"]]
    item_by_id = {item["anchor_id"]: item for item in all_items}
    if len(item_by_id) != len(all_items):
        raise ValueError("Review queue anchor IDが重複しています")
    if set(item_by_id) != set(anchor_ids):
        raise ValueError("Eligible raw anchorがReview queueへ完全投影されていません")
    empty_ledger = {
        "schema_version": 1, "atlas_id": "rabbitmq-reference-atlas", "queue_id": queue_id,
        "status": "incomplete-human-review-required", "decisions": [],
    }
    ledger = read(DECISION_LEDGER_PATH) if DECISION_LEDGER_PATH.exists() else empty_ledger
    if (ledger.get("schema_version") != 1 or ledger.get("atlas_id") != "rabbitmq-reference-atlas"
            or ledger.get("queue_id") != queue_id or ledger.get("status") != "incomplete-human-review-required"):
        raise ValueError("Review decision ledger identity/statusが現在のqueueと一致しません")
    catalog = result_catalog()
    decided = validate_decisions(ledger["decisions"], item_by_id, catalog)
    promotion_baseline = read(PROMOTION_BASELINE_PATH)
    exact(promotion_baseline, {"schema_version", "id", "captured_commit", "authority_surface_ids", "atomic_behavior_ids"}, "Authority review promotion baseline")
    if (promotion_baseline["schema_version"] != 1
            or promotion_baseline["id"] != "authority-review-prequeue-v1-2026-08-28"
            or promotion_baseline["captured_commit"] != "a40cc2a37b6fe69ba9475b3a19d398ff23b6c066"):
        raise ValueError("Authority review promotion baseline identityが不正です")
    baseline_catalog = {
        "authority-surface": set(promotion_baseline["authority_surface_ids"]),
        "atomic-behavior": set(promotion_baseline["atomic_behavior_ids"]),
    }
    decision_results = {
        (result["item_type"], result["id"])
        for decision in ledger["decisions"] for result in decision["result_items"]
    }
    validate_promotion_floor(catalog, baseline_catalog, decision_results)
    priority_counts = Counter(item["proposed_priority"] for item in all_items)
    clusters = {item["proposed_candidate_cluster_id"] for item in all_items if item["proposed_candidate_cluster_id"]}
    stale_holds = []
    for artifact in artifacts:
        if artifact["fetch"]["status"] == "stale":
            stale_holds.append({
                "document_id": artifact["document_id"], "document_url": artifact["fetch_url"],
                "source_ids": artifact["source_ids"], "locked_source_digest": artifact["locked_body_digest"],
                "inventory_tool_digest": artifact["extraction"]["tool_digest"],
                "review_queue_tool_digest": queue_tool_digest, "locator": "document-root",
                "fetched_digest": artifact["fetch"]["fetched_digest"],
                "status": "hold-stale-document-relock-required",
                "reason": "locked-document-body-digest-mismatch",
            })
    stale_holds.sort(key=lambda item: item["document_id"])
    index = {
        "schema_version": 1, "atlas_id": "rabbitmq-reference-atlas", "generated_at": GENERATED_AT,
        "status": "incomplete-human-review-required", "queue_id": queue_id,
        "input_digest": input_digest, "tool_digest": queue_tool_digest,
        "decision_ledger": DECISION_LEDGER_PATH.relative_to(ROOT).as_posix(),
        "body_storage": "digest-locator-and-offset-only",
        "machine_assistance": "dedupe-candidate-cluster-priority-and-batch-proposals-only",
        "semantic_decisions": "human-primary-source-review-only",
        "semantic_accounting": {
            "queued_anchor_count_toward_semantic_surface": False,
            "queued_anchor_count_toward_depth": False,
            "priority_cluster_batch_are": "machine-proposals-only",
            "promotion_requires": "valid-human-primary-source-decision",
        },
        "summary": {
            "eligible_documents": sum(artifact["fetch"]["status"] == "matched" for artifact in artifacts),
            "queued_anchors": len(all_items), "pending_human": len(all_items) - len(decided),
            "human_reviewed": len(decided),
            "proposed_priority_counts": {str(priority): priority_counts[priority] for priority in (0, 1, 2)},
            "proposed_candidate_clusters": len(clusters),
            "clustered_anchor_proposals": sum(item["proposed_candidate_cluster_id"] is not None for item in all_items),
            "proposed_batches": len(batches), "stale_document_holds": len(stale_holds),
            "decisions": len(ledger["decisions"]),
            "included": sum(decision["action"] == "include" for decision in ledger["decisions"]),
            "excluded": sum(decision["action"] == "exclude" for decision in ledger["decisions"]),
            "merged": sum(decision["action"] == "merge" for decision in ledger["decisions"]),
            "split": sum(decision["action"] == "split" for decision in ledger["decisions"]),
            "promoted_authority_surfaces": sum(
                result["item_type"] == "authority-surface" for decision in ledger["decisions"] for result in decision["result_items"]
            ),
            "promoted_atomic_behaviors": sum(
                result["item_type"] == "atomic-behavior" for decision in ledger["decisions"] for result in decision["result_items"]
            ),
            "authority_semantics_exhaustive": False,
        },
        "batches": [{
            "id": batch["batch_id"], "path": f"authority/review-queue-draft/{batch['batch_id']}.json",
            "digest": artifact_digest(batch), "proposed_priority": batch["items"][0]["proposed_priority"],
            "selector_kind": batch["items"][0]["selector_kind"],
            "bucket": batch["batch_id"].rsplit("-", 1)[1], "items": len(batch["items"]),
        } for batch in batches],
        "stale_holds": stale_holds,
    }
    return index, batches, empty_ledger


def write_queue() -> dict:
    index, batches, empty_ledger = build_queue()
    QUEUE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    expected_files = {f"{batch['batch_id']}.json" for batch in batches}
    unexpected = {path.name for path in QUEUE_DIRECTORY.glob("*.json")} - expected_files
    if unexpected:
        raise ValueError(f"MigrationなしのReview batch削除・置換を拒否します: {sorted(unexpected)}")
    for batch in batches:
        (QUEUE_DIRECTORY / f"{batch['batch_id']}.json").write_text(
            json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    DECISION_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DECISION_LEDGER_PATH.exists():
        DECISION_LEDGER_PATH.write_text(json.dumps(empty_ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    QUEUE_INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return index


def verify_queue() -> dict:
    expected_index, expected_batches, _ = build_queue()
    actual_index = read(QUEUE_INDEX_PATH)
    exact(actual_index, {
        "schema_version", "atlas_id", "generated_at", "status", "queue_id", "input_digest",
        "tool_digest", "decision_ledger", "body_storage", "machine_assistance", "semantic_decisions",
        "semantic_accounting", "summary", "batches", "stale_holds",
    }, "Review queue index")
    if actual_index != expected_index:
        raise ValueError("Authority review queue indexが入力/tool/batch期待値と一致しません")
    expected_files = sorted(f"{batch['batch_id']}.json" for batch in expected_batches)
    actual_files = sorted(path.name for path in QUEUE_DIRECTORY.glob("*.json"))
    if actual_files != expected_files:
        raise ValueError("Authority review batch file集合が不正です")
    item_ids = set()
    for expected_batch in expected_batches:
        batch = read(QUEUE_DIRECTORY / f"{expected_batch['batch_id']}.json")
        exact(batch, {"schema_version", "queue_id", "batch_id", "status", "machine_assistance", "semantic_decisions", "items"}, f"Review batch {batch.get('batch_id')}")
        if batch != expected_batch:
            raise ValueError(f"Review batchが決定論生成値と一致しません: {batch['batch_id']}")
        for item in batch["items"]:
            exact(item, QUEUE_ITEM_KEYS, f"Review item {item.get('anchor_id')}")
            if item["anchor_id"] in item_ids or item["state"] != "pending-human":
                raise ValueError(f"Review queue stable ID/state mismatch: {item['anchor_id']}")
            item_ids.add(item["anchor_id"])
    body_anchors = {
        anchor["id"] for path in BODY_DIRECTORY.glob("*.json")
        for anchor in read(path)["anchors"]
    }
    if item_ids != body_anchors:
        raise ValueError("全eligible raw anchorがQueueへ一度ずつ投影されていません")
    ledger = read(DECISION_LEDGER_PATH)
    exact(ledger, {"schema_version", "atlas_id", "queue_id", "status", "decisions"}, "Review decision ledger")
    decided = validate_decisions(ledger["decisions"], {
        item["anchor_id"]: item for batch in expected_batches for item in batch["items"]
    })
    if (len(item_ids) != actual_index["summary"]["queued_anchors"]
            or len(decided) != actual_index["summary"]["human_reviewed"]
            or actual_index["summary"]["pending_human"] != len(item_ids) - len(decided)):
        raise ValueError("Review queue pending/human summary mismatch")
    accounting = actual_index["semantic_accounting"]
    if (accounting["queued_anchor_count_toward_semantic_surface"] is not False
            or accounting["queued_anchor_count_toward_depth"] is not False
            or accounting["priority_cluster_batch_are"] != "machine-proposals-only"):
        raise ValueError("Queue件数・machine proposalをSemantic/Depth達成へ算入できません")
    print(
        f"authority-review-queue PASS: anchors={len(item_ids)} batches={actual_index['summary']['proposed_batches']} "
        f"clusters={actual_index['summary']['proposed_candidate_clusters']} stale_holds={actual_index['summary']['stale_document_holds']} "
        f"pending_human={actual_index['summary']['pending_human']} human_decisions={len(ledger['decisions'])} depth_credit=0"
    )
    return actual_index
