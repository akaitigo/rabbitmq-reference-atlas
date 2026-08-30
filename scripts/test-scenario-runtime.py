#!/usr/bin/env python3
"""security-001 wire encoderとEvidence redaction境界を負のfixtureで固定する。"""

from __future__ import annotations

import importlib.util
import tempfile
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_scenario_runtime", ROOT / "scripts/generate-scenario-runtime.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("scenario runtime moduleをloadできません")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)

password = "fixture-secret-must-not-be-recorded"
frame = runtime.sasl_plain_init_frame("fixture-user", password)
assert struct.unpack(">I", frame[:4])[0] == len(frame)
assert frame[4:8] == bytes((2, 1, 0, 0))
assert frame[8:11] == bytes((0, 0x53, 0x41))
assert b"PLAIN" in frame
assert password.encode() in frame

ok_frame = bytes.fromhex("0000001002010000005344c003015000")
auth_frame = bytes.fromhex("0000001002010000005344c003015001")
assert runtime.sasl_outcome(ok_frame) == (0, None)
assert runtime.sasl_outcome(auth_frame) == (1, None)
assert runtime.sasl_outcome(bytes.fromhex("0000000802010000"))[0] is None
assert runtime.dt.datetime.fromisoformat("2026-08-30T15:59:50+00:00").tzinfo is not None

for compose in (runtime.COMPOSE, runtime.SECURITY_COMPOSE):
    assert all(not Path(item).is_absolute() for item in compose)

assert [node["proof_variant"] for node in runtime.LDAP_NODES] == [
    "node-1-with-ldap",
    "node-2-with-ldap",
    "node-3-with-ldap",
]

with tempfile.TemporaryDirectory(prefix="rabbitmq-ldap-variant-") as directory:
    original_evidence_root = runtime.EVIDENCE_ROOT
    runtime.EVIDENCE_ROOT = Path(directory)
    proof = runtime.artifact_paths("ldap.authentication", "security", "node-1-with-ldap")
    legacy = runtime.artifact_paths("ldap.authentication", "security", "node-1")
    assert proof != legacy
    for index, path in enumerate(proof):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"proof-{index}\n", encoding="utf-8")
    runtime.refresh_legacy_variant_aliases(
        "ldap.authentication", "security", "node-1", "node-1-with-ldap"
    )
    assert [path.read_bytes() for path in legacy] == [path.read_bytes() for path in proof]
    runtime.EVIDENCE_ROOT = original_evidence_root

print("scenario-runtime tests PASS: SASL frame/outcome・相対Compose・LDAP正本Variant IDを確認")
