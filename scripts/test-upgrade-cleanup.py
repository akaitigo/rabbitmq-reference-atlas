#!/usr/bin/env python3
"""Upgrade Lab失敗時もtask-owned volumeを削除対象にする負のfixture。"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="rabbitmq-upgrade-cleanup-") as directory:
    temporary = Path(directory)
    calls = temporary / "docker.calls"
    fake_docker = temporary / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >>\"$UPGRADE_CLEANUP_TEST_LOG\"\n"
        "if [[ \" $* \" == *\" up \"* ]]; then exit 23; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    environment = os.environ.copy()
    environment.update({
        "PATH": f"{temporary}:{environment['PATH']}",
        "RABBITMQ_EVIDENCE_RUN_TOKEN": "negative-cleanup-fixture",
        "RABBITMQ_EVIDENCE_ROOT": str(temporary / "evidence"),
        "UPGRADE_CLEANUP_TEST_LOG": str(calls),
    })
    result = subprocess.run(
        ["bash", "scripts/run-upgrade-migration-lab.sh"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23, (result.returncode, result.stdout, result.stderr)
    invocations = calls.read_text(encoding="utf-8").splitlines()
    init_indexes = [
        next(index for index, call in enumerate(invocations) if " run --rm --no-deps --user 0:0 --entrypoint bash " in f" {call}" and service in call)
        for service in ("upgrade-1", "upgrade-2", "upgrade-3")
    ]
    assert any("999:999:400" in call for call in invocations), invocations
    up_index = next(index for index, call in enumerate(invocations) if " up -d --wait" in f" {call}")
    assert max(init_indexes) < up_index, invocations
    ps_index = next(index for index, call in enumerate(invocations) if " ps --all" in f" {call}")
    logs_index = next(index for index, call in enumerate(invocations) if " logs --no-color --timestamps --tail 300" in f" {call}")
    down_index = next(index for index, call in enumerate(invocations) if " down --volumes --remove-orphans" in f" {call}")
    assert ps_index < logs_index < down_index, invocations
    assert all("prune" not in call for call in invocations), invocations

print("upgrade cleanup negative fixture PASS: cookie権限検証、failure logs、task-owned volume削除を固定")
