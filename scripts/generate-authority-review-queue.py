#!/usr/bin/env python3
"""Authority raw anchorのhuman review queueを決定論生成する。"""

from authority_review_queue import write_queue


if __name__ == "__main__":
    index = write_queue()
    print(
        f"generated Authority review queue: anchors={index['summary']['queued_anchors']} "
        f"batches={index['summary']['proposed_batches']} stale_holds={index['summary']['stale_document_holds']}"
    )
