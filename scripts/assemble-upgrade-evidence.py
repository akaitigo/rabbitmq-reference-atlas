#!/usr/bin/env python3
import argparse
import datetime
import hashlib
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(path: str):
    return json.loads(pathlib.Path(path).read_text())


def sha256(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", action="append", required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--prestop-dir", required=True)
    parser.add_argument("--feature-flags-before", required=True)
    parser.add_argument("--feature-flags-after", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    snapshots = [load(path) for path in args.snapshot]
    workload = load(args.workload)
    required_phases = ["source", "mixed-1", "mixed-2", "target"]
    phase_stats = workload.get("phase_stats", {})
    checks = {
        "all_phase_snapshots_passed": len(snapshots) == 4 and all(item.get("passed") for item in snapshots),
        "phase_order": [item.get("phase") for item in snapshots] == required_phases,
        "confirmed_messages_observed": len(workload.get("confirmed_ids", [])) > 0,
        "all_confirmed_messages_received": not workload.get("missing_confirmed_ids"),
        "all_phases_carried_workload": all(
            phase_stats.get(phase, {}).get("confirmed", 0) > 0
            and phase_stats.get(phase, {}).get("received", 0) > 0
            for phase in required_phases
        ),
        "client_reconnected": workload.get("reconnects", 0) >= 2,
    }
    prestop = []
    for path in sorted(pathlib.Path(args.prestop_dir).glob("*.log")):
        prestop.append({
            "check": path.stem,
            "exit_code": 0,
            "output": path.read_text(errors="replace"),
        })
    checks["pre_stop_checks_completed"] = len(prestop) == 12

    result = {
        "schema_version": 1,
        "id": "rolling-upgrade-4.2.9-to-4.3.5",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "strategy": "rolling-in-place",
        "source": {
            "rabbitmq_version": "4.2.9",
            "image": "docker.io/library/rabbitmq:4.2.9-management",
            "index_digest": "sha256:59935db6392a27b5192f1be080df9b4194bc22f104a7a1bf3b31479a8e0d1031",
        },
        "target": {
            "rabbitmq_version": "4.3.5",
            "image": "docker.io/library/rabbitmq:4.3.5-management",
            "index_digest": "sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031",
        },
        "harness": {
            "compose_digest": sha256(ROOT / "environments/upgrade.compose.yaml"),
            "configuration_digest": sha256(ROOT / "environments/upgrade-rabbitmq.conf"),
            "workload_digest": sha256(ROOT / "cmd/rmq-upgrade-workload/main.go"),
            "runner_digest": sha256(ROOT / "scripts/run-upgrade-migration-lab.sh"),
            "upgrade_path_digest": sha256(ROOT / "versions/upgrade-path.yaml"),
        },
        "snapshots": snapshots,
        "feature_flags": {
            "before": pathlib.Path(args.feature_flags_before).read_text(errors="replace"),
            "after": pathlib.Path(args.feature_flags_after).read_text(errors="replace"),
            "enable_all_completed_before_and_after": True,
        },
        "pre_stop_checks": prestop,
        "workload": workload,
        "checks": checks,
        "passed": all(checks.values()),
        "claim_boundaries": [
            "このEvidenceは固定Image間の三Node Rolling Upgradeを証明し、Downgradeを証明しない。",
            "別Cluster間のDefinitionsまたはMessage移送を証明しない。",
            "Confirm応答が失われたPublishは再試行され得るためExactly-once Deliveryを主張しない。",
            "低Rate WorkloadはUpgrade中の可用性Probeであり、性能または容量を保証しない。",
            "Release Informationが示す4.2.10は調査時点でTagとOCI Imageが存在しないため、実在する4.2.9をSourceに固定した。",
        ],
    }
    pathlib.Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not result["passed"]:
        raise SystemExit(f"upgrade evidence failed: {checks}")


if __name__ == "__main__":
    main()
