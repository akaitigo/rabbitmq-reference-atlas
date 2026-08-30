#!/usr/bin/env python3
"""security-002失敗時の診断保存とtask-owned cleanupを負の実行で固定する。"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run-security-002-lab.sh"


with tempfile.TemporaryDirectory(prefix="rabbitmq-security-002-cleanup-") as directory:
    temporary = Path(directory)
    calls = temporary / "docker.calls"
    fake_docker = temporary / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >>\"$SECURITY_002_CLEANUP_TEST_LOG\"\n"
        "if [[ \" $* \" == *\" up -d --wait \"* ]]; then exit 23; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    environment = os.environ.copy()
    environment.update({
        "PATH": f"{temporary}:{environment['PATH']}",
        "TMPDIR": str(temporary),
        "RABBITMQ_EVIDENCE_RUN_TOKEN": "negative-cleanup-fixture",
        "RABBITMQ_EVIDENCE_ROOT": str(temporary / "evidence"),
        "SECURITY_002_CLEANUP_TEST_LOG": str(calls),
    })
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    invocations = calls.read_text(encoding="utf-8").splitlines()
    config_index = next(index for index, call in enumerate(invocations) if " config --quiet" in call)
    up_index = next(index for index, call in enumerate(invocations) if " up -d --wait" in call)
    ps_index = next(index for index, call in enumerate(invocations) if " ps --all" in call)
    logs_index = next(index for index, call in enumerate(invocations) if " logs --no-color --tail 240" in call)
    down_index = next(index for index, call in enumerate(invocations) if " down --volumes --remove-orphans" in call)
    assert config_index < up_index < ps_index < logs_index < down_index, invocations
    assert "security-002 ldap runtime failed" in result.stderr
    assert all("security-002-ldap.compose.yaml" in call for call in invocations), invocations
    assert all("prune" not in call for call in invocations), invocations

source = SCRIPT.read_text(encoding="utf-8")
ldap_down = source.index('"${LDAP_COMPOSE[@]}" down --volumes --remove-orphans\nLDAP_ACTIVE=\'\'')
oauth_config = source.index('"${OAUTH_COMPOSE[@]}" config --quiet')
assert ldap_down < oauth_config
assert 'minimum_free_bytes_before_runtime' in source
assert 'failure_logs ldap' in source and 'failure_logs oauth' in source

print("security-002 cleanup negative fixture PASS: failure logs、逐次実行、task-owned volume削除を固定")
