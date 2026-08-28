#!/usr/bin/env python3
import hashlib
import json
import pathlib

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE_BY_TARGET = {
    "messaging.amqp-model": ["lab.amqp-model"],
    "messaging.exchange-binding-matrix": ["lab.exchange-binding-matrix"],
    "messaging.exchange-routing": ["lab.exchange-queue"],
    "messaging.queue-lifecycle": ["lab.exchange-queue"],
    "delivery.publisher-confirm": ["lab.exchange-queue"],
    "delivery.acknowledgement": ["lab.ack-redelivery"],
    "delivery.redelivery": ["lab.ack-redelivery"],
    "delivery.dead-lettering": ["lab.dead-letter"],
    "delivery.ttl-dead-lettering": ["lab.ttl-dead-letter"],
    "delivery.flow-control": ["lab.consumer-flow-control"],
    "delivery.publisher-flow-control": ["lab.publisher-flow-control"],
    "queue.quorum-stream": ["lab.quorum-stream", "lab.cluster-failure-recovery"],
    "delivery.ordering-idempotency": ["lab.ordering-idempotency"],
    "security.authorization-isolation": ["lab.security-authz"],
    "security.tls-transport": ["lab.security-tls"],
    "observability.management-health": ["lab.observability-health"],
    "observability.queue-state-prometheus": ["lab.observability-state"],
    "performance.fixed-workload": ["lab.performance-capacity"],
    "migration.rolling-upgrade": ["lab.rolling-upgrade"],
    "compatibility.fixed-client-queue-types": ["lab.quorum-stream"],
    "cluster.three-node-membership": ["lab.cluster-membership"],
    "cluster.leader-failure": ["lab.cluster-failure-recovery"],
    "cluster.node-recovery": ["lab.cluster-failure-recovery"],
    "cluster.network-partition-recovery": ["lab.network-partition"],
    "operation.reproducible-evidence": ["operation.evidence-generation"],
    "skill.router-evaluation": ["eval.router"],
    "mastery.foundations-and-decisions": [
        "lab.amqp-model",
        "lab.exchange-binding-matrix",
        "lab.quorum-stream",
        "lab.ordering-idempotency",
    ],
    "mastery.operations-observability": [
        "lab.observability-health",
        "lab.observability-state",
        "lab.publisher-flow-control",
    ],
    "mastery.security-safety": ["lab.security-authz", "lab.security-tls"],
    "mastery.performance-capacity": ["lab.performance-capacity"],
    "mastery.compatibility-integration": ["lab.quorum-stream"],
    "mastery.migration-evolution": ["lab.rolling-upgrade"],
}


def main() -> None:
    path = ROOT / "coverage.yaml"
    coverage = yaml.safe_load(path.read_text())
    for target in coverage["targets"]:
        if target["id"] not in EVIDENCE_BY_TARGET:
            continue
        expected = EVIDENCE_BY_TARGET[target["id"]]
        missing = [evidence_id for evidence_id in expected if not (ROOT / "evidence" / f"{evidence_id}.evidence.json").exists()]
        if missing:
            raise SystemExit(f"{target['id']}: evidence missing: {', '.join(missing)}")
        for evidence_id in expected:
            record = json.loads((ROOT / "evidence" / f"{evidence_id}.evidence.json").read_text())
            artifact = ROOT / record["artifact"]["uri"]
            digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.exists() else "missing"
            if record.get("verdict") != "pass" or digest != record["artifact"]["digest"]:
                raise SystemExit(f"{target['id']}: evidence is not a verified pass: {evidence_id}")
            if not set(target["claim_ids"]).intersection(record.get("claim_ids", [])):
                raise SystemExit(f"{target['id']}: evidence claim mismatch: {evidence_id}")
        target["state"] = "covered"
        target["evidence_ids"] = expected
    path.write_text(yaml.safe_dump(coverage, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()
