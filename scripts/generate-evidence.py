#!/usr/bin/env python3
import datetime
import hashlib
import json
import os
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = pathlib.Path(os.environ.get("RABBITMQ_EVIDENCE_ROOT", ROOT / "evidence"))
RAW = EVIDENCE_ROOT / "raw"


MAPPINGS = [
    ("lab.amqp-model", ["messaging.amqp-model-boundary"], "conformance", "raw/core.json", "container", ["amqp-model-property-equivalence"]),
    ("lab.exchange-binding-matrix", ["messaging.exchange-binding-matrix"], "test-report", "raw/core.json", "container", ["exchange-binding-matrix"]),
    ("lab.exchange-queue", ["messaging.exchange-routing", "messaging.queue-lifecycle", "delivery.publisher-confirm"], "test-report", "raw/core.json", "container", ["exchange-queue-confirm"]),
    ("lab.ack-redelivery", ["delivery.acknowledgement", "delivery.redelivery"], "test-report", "raw/core.json", "container", ["ack-redelivery"]),
    ("lab.dead-letter", ["delivery.dead-lettering"], "test-report", "raw/core.json", "container", ["dead-letter"]),
    ("lab.ttl-dead-letter", ["delivery.ttl-dead-lettering"], "test-report", "raw/core.json", "container", ["ttl-dead-letter"]),
    ("lab.consumer-flow-control", ["delivery.flow-control"], "test-report", "raw/core.json", "container", ["consumer-prefetch"]),
    ("lab.quorum-stream", ["queue.quorum-semantics", "queue.stream-semantics", "compatibility.fixed-client-queue-types"], "conformance", "raw/core.json", "cluster", ["quorum-stream-semantics"]),
    ("lab.ordering-idempotency", ["delivery.ordering-boundary", "delivery.idempotency-boundary"], "test-report", "raw/core.json", "container", ["ordering-idempotency"]),
    ("lab.security-authz", ["security.least-privilege"], "attack", "raw/security-observability.json", "cluster", ["security.bad-password-denied", "security.cross-vhost-denied", "security.authentication", "security.allowed-operations", "security.denied-configure", "security.denied-read", "security.credential-rotation"]),
    ("lab.security-tls", ["security.tls-transport"], "attack", "raw/security-tls.json", "container", ["tls.server-certificate-pinned", "tls.mtls-trusted-ca-and-hostname", "tls.untrusted-ca-rejected", "tls.hostname-mismatch-rejected", "tls.client-certificate-required", "tls.plaintext-listener-disabled"]),
    ("lab.observability-health", ["observability.management-api", "operation.health-and-alarms", "operation.safe-cleanup"], "capture", "raw/security-observability.json", "cluster", ["environment.version-lock-management-authentication", "observability.management-nodes", "observability.management-queue", "operations.node-health.endpoint-1", "operations.node-health.endpoint-2", "operations.node-health.endpoint-3", "operations.cluster-alarms.endpoint-1", "operations.cluster-alarms.endpoint-2", "operations.cluster-alarms.endpoint-3", "operations.safe-cleanup"]),
    ("lab.observability-state", ["observability.queue-state-transition", "observability.prometheus-metrics"], "measurement", "raw/observability-state.json", "cluster", ["environment.rabbitmq-version", "observability.publish-confirm", "observability.state-ready", "observability.state-unacked", "observability.state-acked", "observability.prometheus.rabbitmq-1", "observability.prometheus.rabbitmq-2", "observability.prometheus.rabbitmq-3", "operations.queue-cleanup"]),
    ("lab.performance-capacity", ["performance.fixed-workload"], "benchmark", "raw/performance.json", "cluster", ["performance.classic", "performance.quorum", "performance.stream"]),
    ("lab.publisher-flow-control", ["delivery.publisher-flow-control"], "test-report", "raw/publisher-flow-control.json", "cluster", ["publisher-flow.blocked", "publisher-flow.resumed"]),
    ("lab.rolling-upgrade", ["migration.rolling-upgrade"], "migration", "raw/upgrade-migration.json", "cluster", []),
    ("lab.cluster-membership", ["cluster.three-node-membership"], "conformance", "raw/cluster-before.json", "cluster", ["three-running-members"]),
    ("lab.cluster-failure-recovery", ["cluster.leader-failure", "cluster.node-recovery", "queue.quorum-semantics"], "recovery", "raw/cluster-failure-recovery.json", "cluster", ["quorum-queue-prepared", "leader-failure-delivery", "three-running-members"]),
    ("lab.network-partition", ["cluster.network-partition", "cluster.partition-recovery"], "recovery", "raw/network-partition.json", "cluster", ["quorum-queue-prepared", "partition-minority-write-rejected", "partition-majority-delivery", "partition-replica-rejoined", "three-running-members"]),
    ("eval.router", ["skill.router-evaluation"], "skill-eval", "raw/skill-eval.json", "local", []),
    ("operation.evidence-generation", ["operation.reproducible-evidence"], "conformance", "raw/evidence-generation.json", "local", []),
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


def load_json(relative: str) -> dict:
    path = EVIDENCE_ROOT / relative
    if not path.exists():
        raise SystemExit(f"artifact missing: {path}")
    return json.loads(path.read_text())


def require_passed_checks(relative: str, required: list[str]) -> None:
    document = load_json(relative)
    is_skill_eval = relative == "raw/skill-eval.json"
    if "passed" in document and not is_skill_eval and document.get("passed") is not True:
        raise SystemExit(f"{relative}: top-level passed is not true")
    checks = {}
    for run in document.get("runs", [document]):
        if "passed" in run and not is_skill_eval and run.get("passed") is not True:
            raise SystemExit(f"{relative}: nested run passed is not true")
        run_checks = run.get("checks", [])
        if isinstance(run_checks, dict):
            checks.update(run_checks)
        else:
            for item in run_checks:
                if not isinstance(item, dict):
                    raise SystemExit(f"{relative}: check entry is not an object")
                checks[item.get("name")] = item.get("passed")
    missing = [name for name in required if name not in checks]
    failed = [name for name in required if checks.get(name) is not True]
    if missing or failed:
        raise SystemExit(f"{relative}: missing checks={missing}, failed checks={failed}")
    if is_skill_eval:
        results = document.get("results", [])
        if not results or not all(item.get("passed") is True for item in results) or document.get("passed") != document.get("total"):
            raise SystemExit("router eval raw result is not a complete pass")


def compose_cluster_failure_artifact() -> None:
    runs = [load_json(name) for name in ("raw/prepare-failure.json", "raw/recovery.json", "raw/cluster-after.json")]
    output = {"schema_version": 1, "kind": "composite-recovery", "runs": runs}
    (RAW / "cluster-failure-recovery.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


def compose_network_partition_artifact() -> None:
    runs = [load_json(name) for name in ("raw/partition-prepare.json", "raw/partition-minority.json", "raw/partition-majority.json", "raw/partition-recovery.json", "raw/cluster-after-partition.json")]
    output = {"schema_version": 1, "kind": "composite-network-partition", "runs": runs}
    (RAW / "network-partition.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


def compose_publisher_flow_artifact() -> None:
    runs = [load_json(name) for name in ("raw/publisher-flow-blocked.json", "raw/publisher-flow-resumed.json")]
    output = {"schema_version": 1, "kind": "composite-publisher-flow-control", "runs": runs}
    (RAW / "publisher-flow-control.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    compose_cluster_failure_artifact()
    compose_network_partition_artifact()
    compose_publisher_flow_artifact()
    source_digest = sha(ROOT / "sources.lock.yaml")
    environment_paths = [ROOT / "environments/compose.yaml", ROOT / "environments/rabbitmq.conf", ROOT / "environments/container.lock.yaml"]
    environment_paths.extend(sorted(path for path in (ROOT / "environments").rglob("*") if path.is_file() and path not in environment_paths))
    if (ROOT / "versions/upgrade-path.yaml").exists():
        environment_paths.append(ROOT / "versions/upgrade-path.yaml")
    environment_digest = framed_digest(environment_paths)
    local_digest = framed_digest([ROOT / "atlas.yaml", ROOT / "mastery.yaml", ROOT / "coverage.yaml", ROOT / "skill.package.yaml", ROOT / "versions/baseline.yaml"])
    harness_digest = framed_digest([ROOT / "cmd/rmq-lab/main.go", ROOT / "cmd/rmq-secops/main.go", ROOT / "cmd/rmq-observability/main.go", ROOT / "cmd/rmq-tls-lab/main.go", ROOT / "cmd/rmq-benchmark/main.go", ROOT / "cmd/rmq-flow-control/main.go", ROOT / "cmd/rmq-upgrade-workload/main.go", ROOT / "scripts/run-labs.sh", ROOT / "scripts/evidence_transaction.py", ROOT / "evidence-reporting.yaml", ROOT / "scripts/run-observability-lab.sh", ROOT / "scripts/run-tls-lab.sh", ROOT / "scripts/run-upgrade-migration-lab.sh", ROOT / "scripts/capture-upgrade-snapshot.py", ROOT / "scripts/assemble-upgrade-evidence.py", ROOT / "scripts/generate-evidence.py", ROOT / "go.mod", ROOT / "go.sum"])
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
    # 自己記述Artifactはこのrunで先に生成し、直前runのcopyへ依存させない。
    for _, _, _, relative_artifact, _, required_checks in MAPPINGS:
        require_passed_checks(relative_artifact, required_checks)
    for evidence_id, claims, kind, relative_artifact, profile, _ in MAPPINGS:
        artifact = EVIDENCE_ROOT / relative_artifact
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
        output = EVIDENCE_ROOT / f"{evidence_id}.evidence.json"
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        print(f"generated evidence/{output.name}")


if __name__ == "__main__":
    main()
