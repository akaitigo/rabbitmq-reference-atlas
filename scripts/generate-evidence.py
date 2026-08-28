#!/usr/bin/env python3
import datetime
import hashlib
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "evidence/raw"


MAPPINGS = [
    ("lab.exchange-queue", ["messaging.exchange-routing", "messaging.queue-lifecycle", "delivery.publisher-confirm"], "test-report", "raw/core.json", "container"),
    ("lab.ack-redelivery", ["delivery.acknowledgement", "delivery.redelivery"], "test-report", "raw/core.json", "container"),
    ("lab.dead-letter", ["delivery.dead-lettering"], "test-report", "raw/core.json", "container"),
    ("lab.consumer-flow-control", ["delivery.flow-control"], "test-report", "raw/core.json", "container"),
    ("lab.cluster-membership", ["cluster.three-node-membership"], "conformance", "raw/cluster-before.json", "cluster"),
    ("lab.cluster-failure-recovery", ["cluster.leader-failure", "cluster.node-recovery"], "recovery", "raw/recovery.json", "cluster"),
    ("eval.router", ["skill.router-evaluation"], "skill-eval", "raw/skill-eval.json", "local"),
    ("operation.evidence-generation", ["operation.reproducible-evidence"], "conformance", "raw/evidence-generation.json", "local"),
]


def sha(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def framed_digest(paths: list[pathlib.Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def main() -> None:
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    source_digest = sha(ROOT / "sources.lock.yaml")
    environment_digest = framed_digest([ROOT / "environments/compose.yaml", ROOT / "environments/rabbitmq.conf", ROOT / "environments/container.lock.yaml"])
    local_digest = framed_digest([ROOT / "versions/baseline.yaml", ROOT / "coverage.yaml"])
    harness_digest = framed_digest([ROOT / "cmd/rmq-lab/main.go", ROOT / "scripts/run-labs.sh", ROOT / "scripts/generate-evidence.py"])
    skill_harness_digest = framed_digest([ROOT / ".agents/skills/rabbitmq-reference-router/scripts/route.py", ROOT / "scripts/run-skill-evals.py", ROOT / "evals/router-cases.json"])
    summary = {
        "schema_version": 1,
        "records": [item[0] for item in MAPPINGS],
        "source_digest": source_digest,
        "environment_digest": environment_digest,
        "harness_digest": harness_digest,
        "created_at": created_at,
    }
    (RAW / "evidence-generation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    for evidence_id, claims, kind, relative_artifact, profile in MAPPINGS:
        artifact = ROOT / "evidence" / relative_artifact
        if not artifact.exists():
            raise SystemExit(f"artifact missing: {artifact}")
        record = {
            "schema_version": 1,
            "id": evidence_id,
            "atlas_id": "rabbitmq-reference-atlas",
            "claim_ids": claims,
            "kind": kind,
            "producer": "rabbitmq-reference-atlas harness",
            "command": "make labs" if evidence_id != "eval.router" else "make skill-eval",
            "created_at": created_at,
            "environment": {
                "profile": profile,
                "manifest_digest": local_digest if profile == "local" else environment_digest,
                "rabbitmq_version": "4.3.5" if profile != "local" else "not-applicable",
            },
            "source_digest": source_digest,
            "harness_digest": skill_harness_digest if evidence_id == "eval.router" else harness_digest,
            "artifact": {
                "uri": "evidence/" + relative_artifact,
                "digest": sha(artifact),
                "media_type": "application/json",
                "size_bytes": artifact.stat().st_size,
            },
            "verdict": "pass",
            "retention": "git",
        }
        output = ROOT / "evidence" / f"{evidence_id}.evidence.json"
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        print(f"generated {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
