#!/usr/bin/env python3
"""Authority review decisionのhuman provenanceとmapping整合を検査する。"""

from __future__ import annotations

from authority_review_queue import build_queue, sha, source_binding, validate_decisions, validate_promotion_floor


def expect_failure(decision: dict, item_by_id: dict[str, dict], needle: str) -> None:
    try:
        validate_decisions([decision], item_by_id)
    except ValueError as error:
        assert needle in str(error), str(error)
    else:
        raise AssertionError(f"expected rejection: {needle}")


def main() -> None:
    built, batches, empty_ledger = build_queue()
    items = [item for batch in batches for item in batch["items"]]
    assert len(items) == len({item["anchor_id"] for item in items}) == built["summary"]["queued_anchors"]
    assert all(item["state"] == "pending-human" for item in items)
    assert all(batch["semantic_decisions"] == "none" for batch in batches)
    assert all(item["proposed_batch_id"] == batch["batch_id"] for batch in batches for item in batch["items"])
    assert built["semantic_accounting"]["priority_cluster_batch_are"] == "machine-proposals-only"
    assert built["summary"]["stale_document_holds"] == 2
    assert empty_ledger["decisions"] == []
    assert built["semantic_accounting"]["queued_anchor_count_toward_depth"] is False
    first = items[0]
    reason = "一次資料の固定locatorを人が確認し、独立したProtocol Surfaceとして保持する判断を記録するcontract testです。"
    decision = {
        "decision_id": "decision.contract-test.include", "action": "include",
        "anchor_ids": [first["anchor_id"]], "source_bindings": [source_binding(first)],
        "reason": reason, "reason_digest": sha(reason.encode()), "reviewer": "human-reviewer",
        "reviewed_at": "2026-08-28T12:00:00+09:00", "review_method": "manual-primary-source",
        "mapping": [{"old_anchor_id": first["anchor_id"], "new_item_ids": ["protocol.surface.contract-test"]}],
        "result_items": [{"id": "protocol.surface.contract-test", "item_type": "authority-surface"}],
    }
    item_by_id = {first["anchor_id"]: first}
    catalog = {"authority-surface": {"protocol.surface.contract-test"}, "atomic-behavior": set()}
    assert validate_decisions([decision], item_by_id, catalog) == {first["anchor_id"]}
    expect_failure(dict(decision, reviewer="automated-bot"), item_by_id, "reviewer provenance")
    expect_failure(dict(decision, reviewed_at="2026-08-28T12:00:00"), item_by_id, "timezone")
    expect_failure(dict(decision, review_method="automated"), item_by_id, "一次資料確認")
    expect_failure(dict(decision, reason_digest=sha(b"different")), item_by_id, "reason digest")
    expect_failure(dict(decision, result_items=[]), item_by_id, "mappingとProtocol/Behavior result")
    changed_binding = dict(decision["source_bindings"][0], locator="document-root#drift")
    expect_failure(dict(decision, source_bindings=[changed_binding]), item_by_id, "digest/locator binding")
    baseline_catalog = {"authority-surface": {"existing.surface"}, "atomic-behavior": {"existing.behavior"}}
    promoted_catalog = {"authority-surface": {"existing.surface", "new.surface"}, "atomic-behavior": {"existing.behavior"}}
    try:
        validate_promotion_floor(promoted_catalog, baseline_catalog, set())
    except ValueError as error:
        assert "decisionなし" in str(error)
    else:
        raise AssertionError("new Surface without a human decision must be rejected")
    validate_promotion_floor(promoted_catalog, baseline_catalog, {("authority-surface", "new.surface")})
    print(f"authority-review-queue tests PASS: anchors={len(items)} pending_human={len(items)} stale_holds=2")


if __name__ == "__main__":
    main()
