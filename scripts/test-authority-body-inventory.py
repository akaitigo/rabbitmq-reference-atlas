#!/usr/bin/env python3
"""Authority raw anchor parserの決定性と本文非保存境界を検査する。"""

from __future__ import annotations

import json

from authority_body_inventory import ANCHOR_KEYS, exact, extract_raw_anchors, sha


BODY = b"""# Title\n\n## Child {#child}\n\n<dfn id=term>Term</dfn>\n\n```html\n<h3 id=fake>Fake</h3>\n```\n<!-- <h4 id=comment>Comment</h4> -->\n<table><tr><td>Value</td></tr></table>\n"""


def main() -> None:
    digest = sha(BODY)
    anchors = extract_raw_anchors(BODY, digest, "document-test-example")
    assert anchors == extract_raw_anchors(BODY, digest, "document-test-example")
    assert anchors[0]["id"] != extract_raw_anchors(BODY, digest, "document-test-other")[0]["id"]
    assert len(anchors) == 5
    assert [anchor["selector"] for anchor in anchors] == [
        "document-root", "markdown-atx-heading-1", "markdown-atx-heading-2", "html-dfn", "html-table",
    ]
    assert anchors[2]["locator"] == "#child"
    assert anchors[2]["parent_anchor_id"] == anchors[1]["id"]
    assert all(anchor["classification_status"] == "pending-human" for anchor in anchors)
    assert all(anchor["decision_id"] is None and not anchor["surface_ids"] and not anchor["behavior_ids"] for anchor in anchors)
    rendered = json.dumps(anchors, ensure_ascii=False)
    assert "Title" not in rendered and "Child" not in rendered and "Term" not in rendered and "Value" not in rendered
    assert "fake" not in rendered and "comment" not in rendered
    rejected = dict(anchors[0], body="third-party plaintext")
    try:
        exact(rejected, ANCHOR_KEYS, "negative body fixture")
    except ValueError:
        pass
    else:
        raise AssertionError("Verifier must reject a third-party body field")
    print(f"authority-body parser PASS: anchors={len(anchors)} deterministic=true plaintext=false pending_human={len(anchors)}")


if __name__ == "__main__":
    main()
