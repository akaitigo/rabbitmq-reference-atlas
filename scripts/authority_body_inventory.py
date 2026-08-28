#!/usr/bin/env python3
"""Locked Authority bodyを本文非保存のraw anchor候補へ変換する共通契約。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_DIRECTORY = ROOT / "authority/body-inventory-draft"
INVENTORY_INDEX = ROOT / "authority/body-inventory.snapshot.json"
DECISIONS_PATH = ROOT / "authority/reviews/decisions.json"
BASELINE_PATH = ROOT / "baseline/authority-body-inventory-v1.json"
MIGRATION_PATH = ROOT / "migrations/authority-body-inventory-v1.json"
REPORT_PATH = ROOT / "artifacts/authority-body-non-regression-report.json"
GENERATED_AT = "2026-08-28T00:00:00+09:00"
BASELINE_ID = "authority-body-inventory-v1-2026-08-28"
SELECTOR_CONTRACT = [
    "document-root",
    "markdown-atx-heading-1",
    "markdown-atx-heading-2",
    "markdown-atx-heading-3",
    "markdown-atx-heading-4",
    "markdown-atx-heading-5",
    "markdown-atx-heading-6",
    "html-h1",
    "html-h2",
    "html-h3",
    "html-h4",
    "html-h5",
    "html-h6",
    "html-dfn",
    "html-section",
    "html-article",
    "html-main",
    "html-nav",
    "html-aside",
    "html-table",
    "html-figure",
]
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
ANCHOR_KEYS = {
    "id", "selector", "selector_kind", "tag", "heading_level", "parent_anchor_id",
    "locator", "locator_kind", "context_start", "context_end", "context_unit",
    "context_digest", "label_digest", "classification_status", "decision_id",
    "surface_ids", "behavior_ids",
}


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def exact(value: dict, keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ValueError(f"{label}: 本文field、未知field、または必須field欠落: {sorted(set(value) ^ keys)}")


def canonical_fetch_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def document_id(fetch_url: str) -> str:
    host = re.sub(r"^www\.", "", urlsplit(fetch_url).hostname or "document").replace(".", "-")
    return f"document-{host}-{hashlib.sha256(fetch_url.encode()).hexdigest()[:12]}"


def tool_digest() -> str:
    files = [
        "scripts/authority_body_inventory.py",
        "scripts/extract-authority-body-inventory.py",
        "scripts/test-authority-body-inventory.py",
    ]
    content = b"\0".join(path.encode() + b"\0" + (ROOT / path).read_bytes() for path in files)
    return sha(content)


def collect_inputs() -> dict:
    source_document = yaml.safe_load((ROOT / "sources.lock.yaml").read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = {}
    for source in source_document["sources"]:
        grouped.setdefault(canonical_fetch_url(source["url"]), []).append(source)
    documents = []
    for fetch_url, sources in grouped.items():
        digests = sorted({source["digest"] for source in sources})
        if len(digests) != 1:
            raise ValueError(f"同一document URLに複数のlocked digestがあります: {fetch_url}")
        documents.append({
            "document_id": document_id(fetch_url),
            "fetch_url": fetch_url,
            "locked_digest": digests[0],
            "source_ids": sorted(source["id"] for source in sources),
        })
    documents.sort(key=lambda item: item["document_id"])
    current_tool_digest = tool_digest()
    input_digest = sha(canonical({
        "tool_digest": current_tool_digest,
        "source_entries": len(source_document["sources"]),
        "documents": documents,
    }))
    return {
        "input_digest": input_digest,
        "tool_digest": current_tool_digest,
        "source_entries": len(source_document["sources"]),
        "documents": documents,
    }


def _masked_body(body: bytes) -> bytes:
    masked = bytearray(body)
    patterns = [
        rb"<!--[\s\S]*?-->",
        rb"<script\b[\s\S]*?</script\s*>",
        rb"<style\b[\s\S]*?</style\s*>",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, body, re.I):
            masked[match.start():match.end()] = b" " * (match.end() - match.start())
    offset = 0
    fence: bytes | None = None
    for line in body.splitlines(keepends=True):
        marker = line.lstrip()[:3]
        if marker in {b"```", b"~~~"}:
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            masked[offset:offset + len(line)] = b" " * len(line)
        elif fence is not None:
            masked[offset:offset + len(line)] = b" " * len(line)
        offset += len(line)
    return bytes(masked)


def _attribute(attributes: bytes, name: bytes) -> bytes | None:
    match = re.search(rb"(?:^|\s)" + re.escape(name) + rb"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", attributes, re.I)
    return next((value for value in match.groups() if value is not None), None) if match else None


def _anchor_id(document: str, body_digest: str, selector: str, locator: str, start: int) -> str:
    identity = f"{document}\0{body_digest}\0{selector}\0{locator}\0{start}".encode()
    return "anchor-" + hashlib.sha256(identity).hexdigest()[:20]


def extract_raw_anchors(body: bytes, body_digest: str, document: str) -> list[dict]:
    """固定selectorを列挙する。本文・label文字列は返さない。"""
    masked = _masked_body(body)
    raw: list[dict] = []
    offset = 0
    fence: bytes | None = None
    for line in body.splitlines(keepends=True):
        marker = line.lstrip()[:3]
        if marker in {b"```", b"~~~"}:
            fence = None if fence == marker else (marker if fence is None else fence)
            offset += len(line)
            continue
        match = None if fence else re.match(rb"^(#{1,6})[ \t]+([^\r\n]+)", line)
        if match:
            level = len(match.group(1))
            label = match.group(2)
            explicit = re.search(rb"\{#([^}]+)\}\s*$", label)
            locator = "#" + explicit.group(1).decode("utf-8", errors="replace") if explicit else f"offset:utf8:{offset}"
            raw.append({
                "selector": f"markdown-atx-heading-{level}", "selector_kind": "markdown-atx-heading",
                "tag": f"h{level}", "heading_level": level, "start": offset,
                "end": offset + len(line), "locator": locator,
                "locator_kind": "fragment" if explicit else "locked-body-offset", "label_digest": sha(label),
            })
        offset += len(line)
    html_matcher = re.compile(rb"<(h[1-6]|dfn|section|article|main|nav|aside|table|figure)\b([^>]*)>", re.I)
    for match in html_matcher.finditer(masked):
        tag = match.group(1).decode().lower()
        attributes = match.group(2)
        fragment = _attribute(attributes, b"id") or _attribute(attributes, b"name")
        close = re.search(rb"</" + tag.encode() + rb"\s*>", masked[match.end():], re.I)
        end = match.end() + close.end() if close else match.end()
        locator = "#" + fragment.decode("utf-8", errors="replace") if fragment else f"offset:utf8:{match.start()}"
        label_digest = None
        if re.fullmatch(r"h[1-6]|dfn", tag):
            inner_end = match.end() + close.start() if close else min(len(body), match.end() + 4096)
            label_digest = sha(body[match.end():inner_end])
        raw.append({
            "selector": f"html-{tag}", "selector_kind": "html-element", "tag": tag,
            "heading_level": int(tag[1]) if re.fullmatch(r"h[1-6]", tag) else None,
            "start": match.start(), "end": end, "locator": locator,
            "locator_kind": "fragment" if fragment else "locked-body-offset", "label_digest": label_digest,
        })
    raw.sort(key=lambda item: (item["start"], item["selector"], item["locator"]))

    root_id = _anchor_id(document, body_digest, "document-root", "document-root", 0)
    anchors = [{
        "id": root_id, "selector": "document-root", "selector_kind": "document-root", "tag": "document",
        "heading_level": None, "parent_anchor_id": None, "locator": "document-root",
        "locator_kind": "document-root", "context_start": 0, "context_end": len(body),
        "context_unit": "utf8-byte", "context_digest": body_digest, "label_digest": None,
        "classification_status": "pending-human", "decision_id": None, "surface_ids": [], "behavior_ids": [],
    }]
    heading_stack: dict[int, str] = {}
    for item in raw:
        parent = root_id
        if item["heading_level"] is not None:
            for level in range(item["heading_level"] - 1, 0, -1):
                if level in heading_stack:
                    parent = heading_stack[level]
                    break
        elif heading_stack:
            parent = heading_stack[max(heading_stack)]
        anchor_id = _anchor_id(document, body_digest, item["selector"], item["locator"], item["start"])
        context_start = max(0, item["start"] - 1024)
        context_end = min(len(body), max(item["end"], item["start"] + 1) + 4096)
        anchors.append({
            "id": anchor_id, "selector": item["selector"], "selector_kind": item["selector_kind"],
            "tag": item["tag"], "heading_level": item["heading_level"], "parent_anchor_id": parent,
            "locator": item["locator"], "locator_kind": item["locator_kind"],
            "context_start": context_start, "context_end": context_end, "context_unit": "utf8-byte",
            "context_digest": sha(body[context_start:context_end]), "label_digest": item["label_digest"],
            "classification_status": "pending-human", "decision_id": None, "surface_ids": [], "behavior_ids": [],
        })
        if item["heading_level"] is not None:
            heading_stack[item["heading_level"]] = anchor_id
            for level in range(item["heading_level"] + 1, 7):
                heading_stack.pop(level, None)
    return anchors


def anchor_counts(anchors: list[dict]) -> dict[str, int]:
    return dict(sorted(Counter(anchor["selector"] for anchor in anchors).items()))


def artifact_digest(artifact: dict) -> str:
    return sha((json.dumps(artifact, ensure_ascii=False, indent=2) + "\n").encode())
