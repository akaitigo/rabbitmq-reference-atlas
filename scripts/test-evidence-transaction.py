#!/usr/bin/env python3
"""原子的Evidence publicationの失敗境界を検証する。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from evidence_transaction import begin, finalize, rollback, sha, swap, tree_digest, verify


def fixture(root: Path) -> tuple[Path, Path]:
    live = root / "evidence"
    (live / "raw").mkdir(parents=True)
    (live / "raw/a.json").write_text('{"generation":"old-a"}\n', encoding="utf-8")
    (live / "raw/b.json").write_text('{"generation":"old-b"}\n', encoding="utf-8")
    (live / "a.evidence.json").write_text('{"generation":"old-record"}\n', encoding="utf-8")
    (live / "scenarios").mkdir()
    (live / "scenarios/preserved.json").write_text('{"preserved":true}\n', encoding="utf-8")
    config = root / "evidence-reporting.yaml"
    config.write_text(yaml.safe_dump({
        "publication": {"owned_globs": ["raw/*.json", "*.evidence.json"], "run_report": "run-report.json"}
    }), encoding="utf-8")
    return live, config


def transaction_paths(root: Path, name: str) -> tuple[Path, Path, Path]:
    return root / f"{name}.state.json", root / f".evidence-next-{name}", root / f".evidence-previous-{name}"


def regenerate_all(staging: Path, marker: str = "new") -> None:
    (staging / "raw").mkdir(parents=True, exist_ok=True)
    (staging / "raw/a.json").write_text(f'{{"generation":"{marker}-a"}}\n', encoding="utf-8")
    (staging / "raw/b.json").write_text(f'{{"generation":"{marker}-b"}}\n', encoding="utf-8")
    (staging / "a.evidence.json").write_text(f'{{"generation":"{marker}-record"}}\n', encoding="utf-8")


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    live, config = fixture(root)
    baseline = tree_digest(live)

    # 部分生成はverifyを通らず、liveへ一Byteも反映しない。
    state, staging, backup = transaction_paths(root, "partial")
    begin(state, live, staging, backup, config)
    (staging / "raw").mkdir(parents=True, exist_ok=True)
    (staging / "raw/a.json").write_text('{"generation":"partial"}\n', encoding="utf-8")
    try:
        verify(state)
        raise AssertionError("partial Evidenceがverifyを通過しました")
    except ValueError as error:
        assert "不足" in str(error)
    assert tree_digest(live) == baseline
    rollback(state)
    assert tree_digest(live) == baseline

    # 旧Artifactのcopyによる新旧混在はmtime境界で拒否する。
    state, staging, backup = transaction_paths(root, "mixed")
    begin(state, live, staging, backup, config)
    regenerate_all(staging)
    shutil.copy2(live / "raw/b.json", staging / "raw/b.json")
    os.utime(staging / "raw/b.json", ns=(1, 1))
    try:
        verify(state)
        raise AssertionError("新旧混在Evidenceがverifyを通過しました")
    except ValueError as error:
        assert "混在" in str(error)
    rollback(state)
    assert tree_digest(live) == baseline

    # full-run自体の失敗は直前成功Evidenceを消去しない。
    state, staging, backup = transaction_paths(root, "failed-run")
    begin(state, live, staging, backup, config)
    regenerate_all(staging, "failed")
    rollback(state)
    assert tree_digest(live) == baseline

    # backup rename後のswap失敗は直前成功集合を即時rollbackする。
    state, staging, backup = transaction_paths(root, "swap-failure")
    begin(state, live, staging, backup, config)
    regenerate_all(staging, "swap-failure")
    verify(state)
    try:
        swap(state, "after-backup")
        raise AssertionError("swap failure injectionが失敗しませんでした")
    except OSError:
        pass
    assert tree_digest(live) == baseline
    rollback(state)
    assert tree_digest(live) == baseline

    # swap後のRepository Gate失敗もbackupへ戻し、成功集合を維持する。
    state, staging, backup = transaction_paths(root, "post-swap-validation-failure")
    begin(state, live, staging, backup, config)
    regenerate_all(staging, "post-swap-failure")
    verify(state)
    swap(state)
    rollback(state)
    assert tree_digest(live) == baseline

    # 全件生成後だけ一つのdirectory集合として公開する。
    state, staging, backup = transaction_paths(root, "success")
    begin(state, live, staging, backup, config)
    regenerate_all(staging, "success")
    verify(state)
    swap(state)
    assert sha(live / "raw/a.json") != sha(backup / "raw/a.json")
    assert (live / "scenarios/preserved.json").is_file()
    finalize(state)
    assert not backup.exists()

print("Evidence transaction tests PASS: partial/mixed/failed-run/swap-failureを拒否し、full-runだけを公開")

# Python producerがRABBITMQ_EVIDENCE_ROOT外のlive Evidenceへ書かないことを確認する。
repository_root = Path(__file__).resolve().parents[1]
live_evidence = repository_root / "evidence"
owned_patterns = ("raw/*.json", "*.evidence.json", "dependency-graph.json", "reference-system/*.json", "scenarios/*.json",
                  "scenarios/behaviors/**/*.proof.json", "scenario-runtime/**/*.json", "scenario-runtime/**/*.txt")
owned_before = {item.relative_to(live_evidence).as_posix(): sha(item) for pattern in owned_patterns
                for item in live_evidence.glob(pattern)}
with tempfile.TemporaryDirectory(prefix=".evidence-staging-test-", dir=repository_root) as directory:
    # 実transactionと同様、Evidence root自体をRepository直下の一時directoryにする。
    staged_evidence = Path(directory)
    shutil.copytree(live_evidence / "raw", staged_evidence / "raw", copy_function=shutil.copy2)
    # Python producerだけを隔離検証するため、実Brokerが既に生成したruntime inputは
    # liveへ書き戻さずstagingへ複製する。Closure Plan構造はこのinput集合に依存する。
    shutil.copytree(live_evidence / "scenario-runtime", staged_evidence / "scenario-runtime", copy_function=shutil.copy2)
    environment = os.environ.copy()
    environment["RABBITMQ_EVIDENCE_ROOT"] = str(staged_evidence)
    environment["RABBITMQ_EVIDENCE_ONLY"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for script in ("generate-amqp10-evidence.py", "generate-plugin-protocol-evidence.py", "generate-evidence.py", "generate-scenario-proofs.py", "evidence_dependency_graph.py"):
        arguments = [sys.executable, str(repository_root / "scripts" / script)]
        if script == "evidence_dependency_graph.py":
            arguments.append("generate")
        result = subprocess.run(arguments, cwd=repository_root,
                                env=environment, capture_output=True, text=True)
        if result.returncode != 0:
            diagnostics = ""
            graph_path = staged_evidence / "dependency-graph.json"
            if graph_path.is_file():
                diagnostics = "\ngraph=" + graph_path.read_text(encoding="utf-8")[-3000:]
            closure_path = staged_evidence / "scenarios/closure-plan.json"
            if closure_path.is_file():
                closure = json.loads(closure_path.read_text(encoding="utf-8"))
                diagnostics += (
                    f"\nclosure_summary={closure.get('summary')}"
                    f"\ncompleted_tranches={closure.get('completed_tranches')}"
                )
            proof_path = staged_evidence / "scenarios/behaviors/amqp10.authentication-options/security.proof.json"
            if proof_path.is_file():
                proof = json.loads(proof_path.read_text(encoding="utf-8"))
                diagnostics += f"\nsecurity_proof_gaps={proof.get('scenario_gap_ids')}"
            raise AssertionError(
                f"staging producer failed: {script}\nstdout={result.stdout}\nstderr={result.stderr}{diagnostics}"
            )
    assert len(list(staged_evidence.glob("*.evidence.json"))) == len(list(live_evidence.glob("*.evidence.json")))
    assert len(list((staged_evidence / "scenarios/behaviors").rglob("*.proof.json"))) == 2060
    assert (staged_evidence / "dependency-graph.json").is_file()
    assert (staged_evidence / "scenarios/closure-plan.json").is_file()
    assert (staged_evidence / "reference-system/results.json").is_file()
owned_after = {item.relative_to(live_evidence).as_posix(): sha(item) for pattern in owned_patterns
               for item in live_evidence.glob(pattern)}
assert owned_after == owned_before
print("Evidence staging producer tests PASS: Raw/Record生成先をtransaction stagingへ隔離")
