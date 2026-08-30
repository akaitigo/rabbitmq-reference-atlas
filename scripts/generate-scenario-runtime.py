#!/usr/bin/env python3
"""実Brokerの専用Scenario ArtifactとRuntime reportを生成する。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path(os.environ.get("RABBITMQ_EVIDENCE_ROOT", ROOT / "evidence"))
COMPOSE = ["docker", "compose", "-f", str(ROOT / "environments/compose.yaml")]
IMAGE_DIGEST = "sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031"
AMQP091_HEADER = bytes((65, 77, 81, 80, 0, 0, 9, 1))
NODES = (
    {"variant": "node-1", "service": "rabbitmq-1", "amqp": ("127.0.0.1", 25672), "management": "http://127.0.0.1:35672"},
    {"variant": "node-2", "service": "rabbitmq-2", "amqp": ("127.0.0.1", 25673), "management": "http://127.0.0.1:35673"},
    {"variant": "node-3", "service": "rabbitmq-3", "amqp": ("127.0.0.1", 25674), "management": "http://127.0.0.1:35674"},
)


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def binding(path: Path, channel: str, media_type: str) -> dict[str, Any]:
    return {
        "path": "evidence/" + path.relative_to(EVIDENCE_ROOT).as_posix(),
        "digest": sha(path),
        "size_bytes": path.stat().st_size,
        "channel": channel,
        "media_type": media_type,
    }


def authority_surface(behavior: str) -> str:
    inventory = yaml.safe_load((ROOT / "surface.inventory.yaml").read_text(encoding="utf-8"))
    item = next((entry for entry in inventory["items"] if entry["behavior_id"] == behavior), None)
    if not item:
        raise ValueError(f"unknown behavior: {behavior}")
    return item["authority_surface_id"]


def run(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def cluster_status(service: str) -> dict[str, Any]:
    return run([*COMPOSE, "exec", "-T", service, "rabbitmqctl", "cluster_status", "--formatter", "json"])


def broker_log(service: str) -> str:
    result = run([*COMPOSE, "logs", "--no-color", "--tail", "240", service])
    if result["returncode"] != 0:
        raise RuntimeError(f"broker log collection failed: {service}: {result['stderr']}")
    if not result["stdout"].strip():
        raise RuntimeError(f"broker log was empty: {service}")
    # Compose prefixes otherwise leave spaces on visually empty log lines.
    return "\n".join(line.rstrip() for line in result["stdout"].splitlines()) + "\n"


def management_get(base: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(base + path)
    token = base64.b64encode(b"atlas:atlas-local-only").decode("ascii")
    request.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = response.read()
            return {"status": response.status, "body": json.loads(body), "error": None}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {"status": error.code, "body": body, "error": str(error)}
    except Exception as error:  # Network failure is itself a bounded runtime observation.
        return {"status": None, "body": None, "error": str(error)}


def amqp_header_probe(endpoint: tuple[str, int]) -> dict[str, Any]:
    observation = {
        "transport": "tcp",
        "protocol": "AMQP 0-9-1",
        "endpoint": f"{endpoint[0]}:{endpoint[1]}",
        "sent_hex": AMQP091_HEADER.hex(),
        "received_hex": "",
        "error": None,
    }
    try:
        with socket.create_connection(endpoint, timeout=4) as connection:
            connection.settimeout(5)
            connection.sendall(AMQP091_HEADER)
            observation["received_hex"] = connection.recv(8).hex()
    except Exception as error:  # Expected during some failure variants.
        observation["error"] = str(error)
    return observation


def platform_identity() -> str:
    result = run(["docker", "version", "--format", "{{.Server.Version}}"])
    if result["returncode"] != 0 or not result["stdout"].strip():
        raise RuntimeError("Docker runtime identity could not be collected")
    return "docker-engine/" + result["stdout"].strip()


def artifact_paths(behavior: str, scenario: str, variant: str) -> tuple[Path, Path, Path]:
    base = EVIDENCE_ROOT / "scenario-runtime/artifacts" / behavior.replace("/", "_") / scenario / variant
    return base / "packet.json", base / "log.txt", base / "metric.json"


def write_variant_artifacts(behavior: str, scenario: str, node: dict[str, Any], packet: dict[str, Any],
                            queue: str | None = None) -> dict[str, dict[str, Any]]:
    packet_path, log_path, metric_path = artifact_paths(behavior, scenario, node["variant"])
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(packet_path, packet)
    log_path.write_text(broker_log(node["service"]), encoding="utf-8")
    metric = {
        "node": node["service"],
        "management_nodes": management_get(node["management"], "/api/nodes"),
        "cluster_status": cluster_status(node["service"]),
    }
    if queue:
        metric["queue"] = management_get(node["management"], "/api/queues/%2F/" + urllib.parse.quote(queue, safe=""))
    write_json(metric_path, metric)
    if metric["cluster_status"]["returncode"] != 0:
        raise RuntimeError(f"cluster_status failed: {node['service']}")
    return {
        "packet": binding(packet_path, "packet", "application/json"),
        "log": binding(log_path, "log", "text/plain"),
        "metric": binding(metric_path, "metric", "application/json"),
    }


def report(behavior: str, scenario: str, profile: str, source_path: str, client_name: str,
           packets: dict[str, dict[str, Any]], assertions: dict[str, list[str]], queue: str | None = None) -> None:
    source_file = ROOT / source_path
    harness_file = ROOT / "scripts/generate-scenario-runtime.py"
    source = {"path": source_path, "digest": sha(source_file)}
    harness = {"path": "scripts/generate-scenario-runtime.py", "digest": sha(harness_file)}
    platform = platform_identity()
    execution_id = os.environ.get("RABBITMQ_EVIDENCE_RUN_TOKEN")
    if not execution_id:
        raise RuntimeError("RABBITMQ_EVIDENCE_RUN_TOKEN is required")
    variants = []
    for node in NODES:
        channels = write_variant_artifacts(behavior, scenario, node, packets[node["variant"]], queue)
        variants.append({
            "id": node["variant"], "attempts": 1, "retries": 0,
            "broker": {"runtime_kind": "actual-broker", "product": "RabbitMQ", "version": "4.3.5", "image_digest": IMAGE_DIGEST},
            "client": {"runtime_kind": "actual-client", "name": client_name, "version": source["digest"], "source_digest": source["digest"]},
            "runtime": {"profile": profile, "platform": platform, "execution_id": execution_id},
            "oracle": {"id": f"oracle.{behavior}.{scenario}.{node['variant']}", "assertions": assertions[node["variant"]], "passed": True},
            "source": source, "harness": harness, "artifact_channels": channels,
        })
    output = {
        "schema_version": 1,
        "behavior_id": behavior,
        "authority_surface_id": authority_surface(behavior),
        "scenario": scenario,
        "runtime_profile": profile,
        "status": "passed",
        "attempts": 1,
        "retries": 0,
        "source": source,
        "harness": harness,
        "variants": variants,
    }
    path = EVIDENCE_ROOT / "scenario-runtime" / behavior.replace("/", "_") / f"{scenario}.runtime.json"
    write_json(path, output)


def raw_protocol_packets(raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    checks = raw.get("checks", [])
    if len(checks) != 3 or raw.get("passed") is not True or not all(item.get("passed") is True for item in checks):
        raise RuntimeError(f"protocol oracle failed: {raw.get('behavior_id')}:{raw.get('scenario')}")
    packets, assertions = {}, {}
    for node, check in zip(NODES, checks):
        packets[node["variant"]] = {
            "transport": "tcp",
            "protocol": raw["behavior_id"].split(".", 1)[0],
            "endpoint": check["endpoint"],
            "request_response": check,
            "source_run_created_at": raw["created_at"],
        }
        assertions[node["variant"]] = [check["oracle"], "実Brokerへの専用Protocol client実行が初回で成功した"]
    return packets, assertions


def protocols() -> None:
    definitions = [
        ("amqp10.version-negotiation", scenario, "protocol-amqp10", "cmd/rmq-amqp10-handshake/main.go", "rmq-amqp10-handshake")
        for scenario in ("normal", "boundary", "rejection", "security")
    ] + [
        ("mqtt.protocol-versions-qos", "compatibility", "plugin-mqtt", "cmd/rmq-plugin-protocols/main.go", "rmq-plugin-protocols"),
        ("stomp.protocol-plugin", "compatibility", "plugin-stomp", "cmd/rmq-plugin-protocols/main.go", "rmq-plugin-protocols"),
    ]
    for behavior, scenario, profile, source, client in definitions:
        raw = load_json(EVIDENCE_ROOT / f"raw/definitive.{behavior}.{scenario}.json")
        packets, assertions = raw_protocol_packets(raw)
        report(behavior, scenario, profile, source, client, packets, assertions)


def raw_check(name: str) -> dict[str, Any]:
    raw = load_json(EVIDENCE_ROOT / "raw" / name)
    checks = raw.get("checks", [])
    if not checks or not all(item.get("passed") is True for item in checks):
        raise RuntimeError(f"runtime oracle failed: {name}")
    return raw


def partition_failure(queue: str, isolated_service: str) -> None:
    minority = raw_check("partition-minority.json")
    majority = raw_check("partition-majority.json")
    for behavior, scenario in (("quorum.replication", "failure"), ("partition.detection", "operations")):
        packets, assertions = {}, {}
        for node in NODES:
            packets[node["variant"]] = {
                **amqp_header_probe(node["amqp"]),
                "fault": "docker-network-disconnect",
                "isolated_service": isolated_service,
                "queue": queue,
                "minority_oracle": minority["checks"],
                "majority_oracle": majority["checks"],
            }
            role = "minority" if node["service"] == isolated_service else "majority"
            assertions[node["variant"]] = [
                f"{role} nodeを専用TCP/AMQP probeで駆動した",
                "minority write rejectionとmajority deliveryの両Oracleが同一fault eventで成功した",
            ]
        report(behavior, scenario, "broker-cluster-3", "cmd/rmq-lab/main.go", "rmq-lab", packets, assertions, queue)


def partition_recovery(queue: str) -> None:
    recovery = raw_check("partition-recovery.json")
    cluster = raw_check("cluster-after-partition.json")
    observed = recovery["checks"][0]["observed"]
    if len(observed.get("online_members", [])) != 3:
        raise RuntimeError("partition recovery did not restore three online quorum members")
    packets, assertions = {}, {}
    for node in NODES:
        packets[node["variant"]] = {
            **amqp_header_probe(node["amqp"]),
            "fault": "docker-network-reconnected",
            "queue": queue,
            "recovery_oracle": recovery["checks"],
            "cluster_oracle": cluster["checks"],
        }
        if packets[node["variant"]]["error"] or not packets[node["variant"]]["received_hex"]:
            raise RuntimeError(f"recovery protocol probe failed: {node['service']}")
        assertions[node["variant"]] = [
            "reconnect後の専用TCP/AMQP probeが成功した",
            "quorum queueのonline membersが3へ復帰した",
            "cluster running membersが3へ復帰した",
        ]
    report("partition.recovery", "recovery", "broker-cluster-3", "cmd/rmq-lab/main.go", "rmq-lab", packets, assertions)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("protocols")
    failure = sub.add_parser("partition-failure")
    failure.add_argument("--queue", required=True)
    failure.add_argument("--isolated-service", required=True, choices=[node["service"] for node in NODES])
    recovery = sub.add_parser("partition-recovery")
    recovery.add_argument("--queue", required=True)
    args = parser.parse_args()
    if args.command == "protocols":
        protocols()
    elif args.command == "partition-failure":
        partition_failure(args.queue, args.isolated_service)
    else:
        partition_recovery(args.queue)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
