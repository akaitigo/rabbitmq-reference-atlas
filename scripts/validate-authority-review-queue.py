#!/usr/bin/env python3
"""Authority human review queueとdecision provenanceをoffline検証する。"""

from authority_review_queue import verify_queue


if __name__ == "__main__":
    try:
        verify_queue()
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(str(error)) from error
