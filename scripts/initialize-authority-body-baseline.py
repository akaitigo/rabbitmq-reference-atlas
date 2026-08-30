#!/usr/bin/env python3
"""初回Authority raw anchor集合をimmutable floorとして固定する。"""

from __future__ import annotations

import json

from authority_body_inventory import (
    BASELINE_ID,
    BASELINE_PATH,
    GENERATED_AT,
    INVENTORY_INDEX,
    ROOT,
)


def main() -> None:
    if BASELINE_PATH.exists():
        raise SystemExit(f"{BASELINE_PATH.relative_to(ROOT)}は既に存在します。immutable floorを再生成できません")
    index = json.loads(INVENTORY_INDEX.read_text(encoding="utf-8"))
    documents = []
    for record in index["documents"]:
        artifact = json.loads((ROOT / record["path"]).read_text(encoding="utf-8"))
        documents.append({
            "id": artifact["document_id"], "path": record["path"],
            "locked_body_digest": artifact["locked_body_digest"], "source_ids": artifact["source_ids"],
            "anchor_ids": sorted(anchor["id"] for anchor in artifact["anchors"]),
        })
    documents.sort(key=lambda item: item["id"])
    baseline = {
        "schema_version": 1, "id": BASELINE_ID, "captured_at": GENERATED_AT,
        "input_digest": index["input_digest"], "tool_digest": index["tool_digest"],
        "source_entries": index["summary"]["source_entries"],
        "unique_documents": index["summary"]["unique_documents"],
        "selector_contract": index["selector_contract"], "documents": documents,
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"initialized Authority body baseline: documents={len(documents)} raw_anchors={sum(len(item['anchor_ids']) for item in documents)}")


if __name__ == "__main__":
    main()
