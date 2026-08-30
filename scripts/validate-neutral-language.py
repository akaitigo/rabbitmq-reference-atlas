#!/usr/bin/env python3
"""Reject promotional or author-praise language in current repository content."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BANNED = (
    "世界一",
    "唯一",
    "決定版",
    "作者称賛",
    "akaitigo氏",
    "akaitigoさん",
    "best-in-class",
    "world-class",
    "ultimate reference",
)
IMMUTABLE_DATA = (
    "baseline/",
    "evidence/history/",
    "evidence/historical/",
    "evidence/completion-certificate.json",
)
AKAITIGO_TECHNICAL_PATHS = (
    ".github/workflows/",
    "atlas.yaml",
    "go.mod",
    "sbom.spdx.json",
    "third_party/",
    "scripts/atlas-validate.sh",
    "migrations/",
    "evidence/migrations/",
)


def main() -> int:
    listed = subprocess.check_output(
        ["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, text=True
    ).splitlines()
    failures = []
    scanned = 0
    for relative in sorted(set(listed)):
        if relative in ("LICENSE", "scripts/validate-neutral-language.py") or relative.startswith(IMMUTABLE_DATA):
            continue
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        scanned += 1
        folded = text.casefold()
        for term in BANNED:
            if term.casefold() in folded:
                failures.append(f"{relative}: banned promotional term {term!r}")
        if "akaitigo" in folded and not relative.startswith(AKAITIGO_TECHNICAL_PATHS):
            failures.append(f"{relative}: akaitigo is outside an allowed technical namespace/URL/command path")
    for failure in failures:
        print(f"ERROR: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"neutral-language PASS: files={scanned} promotional_terms=0 author_praise=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
