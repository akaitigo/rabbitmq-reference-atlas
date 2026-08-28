#!/usr/bin/env python3
import pathlib

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE_BY_TARGET = {
    "messaging.exchange-routing": ["lab.exchange-queue"],
    "messaging.queue-lifecycle": ["lab.exchange-queue"],
    "delivery.publisher-confirm": ["lab.exchange-queue"],
    "delivery.acknowledgement": ["lab.ack-redelivery"],
    "delivery.redelivery": ["lab.ack-redelivery"],
    "delivery.dead-lettering": ["lab.dead-letter"],
    "delivery.flow-control": ["lab.consumer-flow-control"],
    "cluster.three-node-membership": ["lab.cluster-membership"],
    "cluster.leader-failure": ["lab.cluster-failure-recovery"],
    "cluster.node-recovery": ["lab.cluster-failure-recovery"],
    "operation.reproducible-evidence": ["operation.evidence-generation"],
    "skill.router-evaluation": ["eval.router"],
}


def main() -> None:
    path = ROOT / "coverage.yaml"
    coverage = yaml.safe_load(path.read_text())
    for target in coverage["targets"]:
        expected = EVIDENCE_BY_TARGET[target["id"]]
        missing = [evidence_id for evidence_id in expected if not (ROOT / "evidence" / f"{evidence_id}.evidence.json").exists()]
        if missing:
            raise SystemExit(f"{target['id']}: evidence missing: {', '.join(missing)}")
        target["state"] = "covered"
        target["evidence_ids"] = expected
    path.write_text(yaml.safe_dump(coverage, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()
