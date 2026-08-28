#!/usr/bin/env python3
"""実行Evidence集合をstagingからdirectory renameで原子的に公開する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import yaml


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".next")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "sha256:" + digest.hexdigest()
    for item in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = item.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def validate_roots(live: Path, staging: Path, backup: Path) -> None:
    roots = [item.resolve() for item in (live, staging, backup)]
    if len(set(roots)) != 3 or len({item.parent for item in roots}) != 1:
        raise ValueError("live/staging/backupは同一親Directoryの異なるPathでなければなりません。")
    if any(item == Path(item.anchor) or len(item.parts) < 3 for item in roots):
        raise ValueError("Evidence transactionの対象Pathが広すぎます。")


def matches(root: Path, globs: list[str]) -> list[str]:
    return sorted({item.relative_to(root).as_posix() for pattern in globs for item in root.glob(pattern)
                   if item.is_file() or item.is_symlink()})


def remove_owned_from_staging(staging: Path, owned: list[str], run_report: str) -> None:
    for relative in [*owned, run_report]:
        candidate = staging / relative
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()


def begin(state_path: Path, live: Path, staging: Path, backup: Path, config_path: Path) -> dict:
    validate_roots(live, staging, backup)
    if state_path.exists() or staging.exists() or backup.exists():
        raise FileExistsError("Evidence transactionのstate/staging/backupが既に存在します。")
    if not live.is_dir():
        raise FileNotFoundError(f"live Evidence rootがありません: {live}")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    globs = config["publication"]["owned_globs"]
    baseline_owned = matches(live, globs)
    if not baseline_owned:
        raise ValueError("run-owned Evidence baselineが空です。")
    started_at_ns = time.time_ns()
    try:
        shutil.copytree(live, staging, copy_function=shutil.copy2)
        remove_owned_from_staging(staging, baseline_owned, config["publication"]["run_report"])
        state = {
            "schema_version": 1,
            "phase": "staging",
            "started_at_ns": started_at_ns,
            "live": str(live.resolve()),
            "staging": str(staging.resolve()),
            "backup": str(backup.resolve()),
            "config": str(config_path.resolve()),
            "config_digest": sha(config_path),
            "reporter_digest": sha(Path(__file__).resolve()),
            "baseline_tree_digest": tree_digest(live),
            "baseline_owned_paths": baseline_owned,
        }
        write_json(state_path, state)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return state


def verify(state_path: Path) -> dict:
    state = read_json(state_path)
    if state["phase"] not in {"staging", "verified"}:
        raise ValueError(f"verifyできないtransaction phaseです: {state['phase']}")
    staging = Path(state["staging"])
    config = yaml.safe_load(Path(state["config"]).read_text(encoding="utf-8"))
    owned = matches(staging, config["publication"]["owned_globs"])
    missing = sorted(set(state["baseline_owned_paths"]) - set(owned))
    if missing:
        raise ValueError(f"full-run Artifactが不足しています: {missing}")
    artifacts = []
    stale = []
    for relative in owned:
        item = staging / relative
        if item.is_symlink() or not item.is_file():
            raise ValueError(f"run-owned Artifactはregular fileでなければなりません: {relative}")
        if item.stat().st_mtime_ns < state["started_at_ns"]:
            stale.append(relative)
        artifacts.append({"path": relative, "digest": sha(item), "size_bytes": item.stat().st_size})
    if stale:
        raise ValueError(f"直前runから混在したArtifactがあります: {stale}")
    report = {
        "schema_version": 1,
        "id": "rabbitmq-runtime-evidence-run-report-v1",
        "status": "full-run-passed",
        "transaction_started_at_ns": state["started_at_ns"],
        "contract_digest": state["config_digest"],
        "reporter_digest": state["reporter_digest"],
        "retention_contract": {
            "publish_on": "full-run-passed",
            "failed_run": "retain-prior-success",
            "swap": "staged-directory-rename-with-rollback",
            "partial_overwrite": "forbidden",
            "mixed_generation": "forbidden",
        },
        "summary": {"baseline_owned": len(state["baseline_owned_paths"]), "published_owned": len(artifacts)},
        "artifacts": artifacts,
    }
    write_json(staging / config["publication"]["run_report"], report)
    state["phase"] = "verified"
    state["staged_tree_digest"] = tree_digest(staging)
    state["published_owned_paths"] = owned
    write_json(state_path, state)
    return state


def swap(state_path: Path, inject_failure: str | None = None) -> dict:
    state = read_json(state_path)
    if state["phase"] != "verified":
        raise ValueError("verify完了前のEvidenceは公開できません。")
    live, staging, backup = (Path(state[key]) for key in ("live", "staging", "backup"))
    validate_roots(live, staging, backup)
    if backup.exists() or not live.is_dir() or not staging.is_dir():
        raise ValueError("Evidence swap前のDirectory状態が不正です。")
    live.rename(backup)
    try:
        if inject_failure == "after-backup":
            raise OSError("injected swap failure after backup rename")
        staging.rename(live)
        state["phase"] = "swapped"
        write_json(state_path, state)
    except BaseException:
        if live.exists() and backup.exists() and not staging.exists():
            live.rename(staging)
        if not live.exists() and backup.exists():
            backup.rename(live)
        if tree_digest(live) != state["baseline_tree_digest"]:
            raise RuntimeError("Evidence swap失敗後のrollbackで直前成功集合を復元できませんでした。")
        raise
    return state


def rollback(state_path: Path) -> dict:
    state = read_json(state_path)
    if state["phase"] not in {"staging", "verified", "swapped"}:
        raise ValueError(f"rollbackできないtransaction phaseです: {state['phase']}")
    live, staging, backup = (Path(state[key]) for key in ("live", "staging", "backup"))
    validate_roots(live, staging, backup)
    if state["phase"] == "swapped":
        failed = staging
        if failed.exists():
            raise FileExistsError(f"rollback退避先が既に存在します: {failed}")
        live.rename(failed)
        backup.rename(live)
        shutil.rmtree(failed)
    elif state["phase"] in {"staging", "verified"} and staging.exists():
        shutil.rmtree(staging)
    if tree_digest(live) != state["baseline_tree_digest"]:
        raise RuntimeError("rollback後のEvidence集合が直前成功集合と一致しません。")
    state["phase"] = "rolled-back"
    write_json(state_path, state)
    return state


def finalize(state_path: Path) -> dict:
    state = read_json(state_path)
    if state["phase"] != "swapped":
        raise ValueError("swap完了前のEvidence transactionはfinalizeできません。")
    backup = Path(state["backup"])
    validate_roots(Path(state["live"]), Path(state["staging"]), backup)
    if not backup.is_dir():
        raise FileNotFoundError("finalize対象の直前成功backupがありません。")
    shutil.rmtree(backup)
    state["phase"] = "finalized"
    state["published_tree_digest"] = tree_digest(Path(state["live"]))
    write_json(state_path, state)
    return state


def cli() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    begin_parser = subparsers.add_parser("begin")
    begin_parser.add_argument("--state", type=Path, required=True)
    begin_parser.add_argument("--live", type=Path, required=True)
    begin_parser.add_argument("--staging", type=Path, required=True)
    begin_parser.add_argument("--backup", type=Path, required=True)
    begin_parser.add_argument("--config", type=Path, required=True)
    for command in ("verify", "swap", "rollback", "finalize"):
        item = subparsers.add_parser(command)
        item.add_argument("--state", type=Path, required=True)
        if command == "swap":
            item.add_argument("--inject-failure", choices=["after-backup"])
    args = parser.parse_args()
    if args.command == "begin":
        state = begin(args.state, args.live, args.staging, args.backup, args.config)
    elif args.command == "verify":
        state = verify(args.state)
    elif args.command == "swap":
        state = swap(args.state, args.inject_failure)
    elif args.command == "rollback":
        state = rollback(args.state)
    else:
        state = finalize(args.state)
    print(json.dumps({"phase": state["phase"], "staging": state["staging"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
