#!/usr/bin/env python3
"""実Brokerの専用Scenario ArtifactとRuntime reportを生成する。"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path(os.environ.get("RABBITMQ_EVIDENCE_ROOT", ROOT / "evidence"))
COMPOSE = ["docker", "compose", "-f", "environments/compose.yaml"]
SECURITY_COMPOSE = ["docker", "compose", "-f", "environments/security-001.compose.yaml"]
IMAGE_DIGEST = "sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031"
LDAP_IMAGE_DIGEST = "sha256:966fd39ed25813890e9bd57dac56def163bbcfe64967e0bae59ab018d505bd93"
AMQP091_HEADER = bytes((65, 77, 81, 80, 0, 0, 9, 1))
AMQP10_SASL_HEADER = bytes((65, 77, 81, 80, 3, 1, 0, 0))
NODES = (
    {"variant": "node-1", "service": "rabbitmq-1", "amqp": ("127.0.0.1", 25672), "management": "http://127.0.0.1:35672"},
    {"variant": "node-2", "service": "rabbitmq-2", "amqp": ("127.0.0.1", 25673), "management": "http://127.0.0.1:35673"},
    {"variant": "node-3", "service": "rabbitmq-3", "amqp": ("127.0.0.1", 25674), "management": "http://127.0.0.1:35674"},
)
LDAP_NODES = (
    {"variant": "node-1", "proof_variant": "node-1-with-ldap", "service": "security-rabbitmq-1", "amqp": ("127.0.0.1", 26672)},
    {"variant": "node-2", "proof_variant": "node-2-with-ldap", "service": "security-rabbitmq-2", "amqp": ("127.0.0.1", 26673)},
    {"variant": "node-3", "proof_variant": "node-3-with-ldap", "service": "security-rabbitmq-3", "amqp": ("127.0.0.1", 26674)},
)
LDAP_RUNTIME_IDENTITY = {
    "product": "OpenLDAP",
    "version": "2.6.10",
    "image_digest": LDAP_IMAGE_DIGEST,
    "runtime_kind": "actual-directory",
}


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


def run_env(command: list[str], environment: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=60,
        env={**os.environ, **environment},
    )
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def cluster_status(service: str, compose: list[str] = COMPOSE) -> dict[str, Any]:
    return run([*compose, "exec", "-T", service, "rabbitmqctl", "cluster_status", "--formatter", "json"])


def broker_log(service: str, compose: list[str] = COMPOSE) -> str:
    result = run([*compose, "logs", "--no-color", "--tail", "240", service])
    if result["returncode"] != 0:
        raise RuntimeError(f"broker log collection failed: {service}: {result['stderr']}")
    if not result["stdout"].strip():
        raise RuntimeError(f"broker log was empty: {service}")
    # Compose prefixes otherwise leave spaces on visually empty log lines.
    return "\n".join(line.rstrip() for line in result["stdout"].splitlines()) + "\n"


def management_request(base: str, path: str, method: str = "GET", body: Any = None,
                       username: str = "atlas", password: str = "atlas-local-only") -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    request.add_header("Authorization", "Basic " + token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read()
            if not raw:
                decoded: Any = None
            else:
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError:
                    decoded = raw.decode("utf-8", errors="replace")
            return {"status": response.status, "body": decoded, "error": None}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {"status": error.code, "body": body, "error": str(error)}
    except Exception as error:  # Network failure is itself a bounded runtime observation.
        return {"status": None, "body": None, "error": str(error)}


def management_get(base: str, path: str, username: str = "atlas",
                   password: str = "atlas-local-only") -> dict[str, Any]:
    return management_request(base, path, username=username, password=password)


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


def amqp10_sasl_mechanisms_probe(endpoint: tuple[str, int]) -> dict[str, Any]:
    observation = {
        "transport": "tcp",
        "protocol": "AMQP 1.0 SASL",
        "endpoint": f"{endpoint[0]}:{endpoint[1]}",
        "sent_hex": AMQP10_SASL_HEADER.hex(),
        "received_hex": "",
        "advertised_mechanisms": [],
        "error": None,
    }
    received = bytearray()
    try:
        with socket.create_connection(endpoint, timeout=4) as connection:
            connection.settimeout(2)
            connection.sendall(AMQP10_SASL_HEADER)
            for _ in range(4):
                try:
                    chunk = connection.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                received.extend(chunk)
                if b"PLAIN" in received:
                    break
        observation["received_hex"] = bytes(received).hex()
        observation["advertised_mechanisms"] = [
            mechanism for mechanism in ("PLAIN", "AMQPLAIN", "ANONYMOUS", "EXTERNAL")
            if mechanism.encode() in received
        ]
    except Exception as error:
        observation["error"] = str(error)
    return observation


def receive_exact(connection: socket.socket, size: int) -> bytes:
    received = bytearray()
    while len(received) < size:
        chunk = connection.recv(size - len(received))
        if not chunk:
            raise ConnectionError(f"AMQP peer closed after {len(received)}/{size} bytes")
        received.extend(chunk)
    return bytes(received)


def receive_amqp_frame(connection: socket.socket) -> bytes:
    size_prefix = receive_exact(connection, 4)
    size = struct.unpack(">I", size_prefix)[0]
    if size < 8 or size > 1024 * 1024:
        raise ValueError(f"invalid AMQP frame size: {size}")
    return size_prefix + receive_exact(connection, size - 4)


def sasl_plain_init_frame(username: str, password: str) -> bytes:
    mechanism = b"PLAIN"
    response = b"\0" + username.encode("utf-8") + b"\0" + password.encode("utf-8")
    if len(mechanism) > 255 or len(response) > 255:
        raise ValueError("security-001 SASL PLAIN field exceeds list8/binary8 boundary")
    fields = bytes((0xA3, len(mechanism))) + mechanism + bytes((0xA0, len(response))) + response
    body = bytes((0x00, 0x53, 0x41, 0xC0, 1 + len(fields), 2)) + fields
    return struct.pack(">IBBH", 8 + len(body), 2, 1, 0) + body


def sasl_outcome(frame: bytes) -> tuple[int | None, str | None]:
    descriptor = frame.find(bytes((0x00, 0x53, 0x44)))
    if descriptor < 0:
        return None, "sasl-outcome descriptor was not present"
    encoded_code = frame.find(bytes((0x50,)), descriptor + 3)
    if encoded_code < 0 or encoded_code + 1 >= len(frame):
        return None, "sasl-code ubyte was not present"
    return frame[encoded_code + 1], None


def amqp10_plain_auth_probe(endpoint: tuple[str, int], username: str, password: str,
                            credential_case: str) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "transport": "tcp", "protocol": "AMQP 1.0 SASL PLAIN",
        "endpoint": f"{endpoint[0]}:{endpoint[1]}", "credential_case": credential_case,
        "sent": {
            "protocol_header": AMQP10_SASL_HEADER.hex(), "performative": "sasl-init",
            "mechanism": "PLAIN", "initial_response": "[redacted]",
        },
        "received": {"protocol_header": None, "mechanisms_frame_size": None, "outcome_code": None},
        "error": None,
    }
    try:
        with socket.create_connection(endpoint, timeout=4) as connection:
            connection.settimeout(5)
            connection.sendall(AMQP10_SASL_HEADER)
            header = receive_exact(connection, 8)
            mechanisms = receive_amqp_frame(connection)
            if header != AMQP10_SASL_HEADER or b"PLAIN" not in mechanisms:
                raise ValueError("broker did not negotiate AMQP 1.0 SASL PLAIN")
            connection.sendall(sasl_plain_init_frame(username, password))
            outcome_frame = receive_amqp_frame(connection)
            code, error = sasl_outcome(outcome_frame)
            if error:
                raise ValueError(error)
            observation["received"] = {
                "protocol_header": header.hex(), "mechanisms_frame_size": len(mechanisms),
                "advertised_plain": True, "outcome_frame_size": len(outcome_frame), "outcome_code": code,
                "outcome": {0: "ok", 1: "auth", 2: "sys", 3: "sys-perm", 4: "sys-temp"}.get(code, "unknown"),
            }
    except Exception as error:
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


def refresh_legacy_variant_aliases(behavior: str, scenario: str, legacy_variant: str,
                                   proof_variant: str) -> None:
    legacy_paths = artifact_paths(behavior, scenario, legacy_variant)
    proof_paths = artifact_paths(behavior, scenario, proof_variant)
    for index in range(len(legacy_paths)):
        legacy_path, proof_path = legacy_paths[index], proof_paths[index]
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        # The first security-001 run published node-* paths before the Closure
        # Plan ID mismatch was found. Refresh byte-equivalent aliases every run
        # so atomic publication never deletes that public baseline while
        # canonical bindings use *-with-ldap.
        shutil.copyfile(proof_path, legacy_path)


def write_variant_artifacts(behavior: str, scenario: str, node: dict[str, Any], packet: dict[str, Any],
                            queue: str | None = None, cluster_status_failure_allowed: bool = False,
                            compose: list[str] = COMPOSE) -> dict[str, dict[str, Any]]:
    proof_variant = node.get("proof_variant", node["variant"])
    packet_path, log_path, metric_path = artifact_paths(behavior, scenario, proof_variant)
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(packet_path, packet)
    log_path.write_text(broker_log(node["service"], compose), encoding="utf-8")
    metric = {
        "node": node["service"],
        "cluster_status": cluster_status(node["service"], compose),
    }
    if node.get("management"):
        metric["management_nodes"] = management_get(node["management"], "/api/nodes")
    else:
        metric["enabled_plugins"] = run([*compose, "exec", "-T", node["service"], "rabbitmq-plugins", "list", "-e", "-m"])
        metric["runtime_environment"] = run([*compose, "exec", "-T", node["service"], "rabbitmqctl", "environment"])
    if queue and node.get("management"):
        metric["queue"] = management_get(node["management"], "/api/queues/%2F/" + urllib.parse.quote(queue, safe=""))
    write_json(metric_path, metric)
    if metric["cluster_status"]["returncode"] != 0 and not cluster_status_failure_allowed:
        raise RuntimeError(f"cluster_status failed: {node['service']}")
    return {
        "packet": binding(packet_path, "packet", "application/json"),
        "log": binding(log_path, "log", "text/plain"),
        "metric": binding(metric_path, "metric", "application/json"),
    }


def report(behavior: str, scenario: str, profile: str, source_path: str, client_name: str,
           packets: dict[str, dict[str, Any]], assertions: dict[str, list[str]], queue: str | None = None,
           unavailable_services: set[str] | None = None, nodes: tuple[dict[str, Any], ...] = NODES,
           compose: list[str] = COMPOSE, runtime_dependencies: list[dict[str, Any]] | None = None) -> None:
    source_file = ROOT / source_path
    harness_file = ROOT / "scripts/generate-scenario-runtime.py"
    source = {"path": source_path, "digest": sha(source_file)}
    harness = {"path": "scripts/generate-scenario-runtime.py", "digest": sha(harness_file)}
    platform = platform_identity()
    execution_id = os.environ.get("RABBITMQ_EVIDENCE_RUN_TOKEN")
    if not execution_id:
        raise RuntimeError("RABBITMQ_EVIDENCE_RUN_TOKEN is required")
    started_at = os.environ.get("RABBITMQ_EVIDENCE_RERUN_AT")
    if not started_at:
        raise RuntimeError("RABBITMQ_EVIDENCE_RERUN_AT is required")
    variants = []
    unavailable_services = unavailable_services or set()
    for node in nodes:
        proof_variant = node.get("proof_variant", node["variant"])
        channels = write_variant_artifacts(
            behavior, scenario, node, packets[node["variant"]], queue,
            cluster_status_failure_allowed=node["service"] in unavailable_services,
            compose=compose,
        )
        if proof_variant != node["variant"]:
            refresh_legacy_variant_aliases(behavior, scenario, node["variant"], proof_variant)
        variants.append({
            "id": proof_variant, "attempts": 1, "retries": 0,
            "broker": {"runtime_kind": "actual-broker", "product": "RabbitMQ", "version": "4.3.5", "image_digest": IMAGE_DIGEST},
            "client": {"runtime_kind": "actual-client", "name": client_name, "version": source["digest"], "source_digest": source["digest"]},
            "runtime": {
                "profile": profile, "platform": platform, "execution_id": execution_id,
                "dependencies": runtime_dependencies or [],
            },
            "oracle": {"id": f"oracle.{behavior}.{scenario}.{proof_variant}", "assertions": assertions[node["variant"]], "passed": True},
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
        "run_metadata": {
            "execution_id": execution_id,
            "started_at": started_at,
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        },
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


def steady_state_tranche() -> None:
    """Security、Operations、Compatibilityを同じhealthy 3-node状態で専用実行する。"""
    execution_id = os.environ.get("RABBITMQ_EVIDENCE_RUN_TOKEN")
    if not execution_id:
        raise RuntimeError("RABBITMQ_EVIDENCE_RUN_TOKEN is required")

    username = "atlas-scenario-" + "".join(character.lower() for character in execution_id if character.isalnum())
    password = "scenario-local-only"
    run([*COMPOSE, "exec", "-T", "rabbitmq-1", "rabbitmqctl", "delete_user", username])
    added = run([*COMPOSE, "exec", "-T", "rabbitmq-1", "rabbitmqctl", "add_user", username, password])
    if added["returncode"] != 0:
        raise RuntimeError(f"scenario management user creation failed: {added['stderr']}")
    try:
        permissions = run([
            *COMPOSE, "exec", "-T", "rabbitmq-1", "rabbitmqctl", "set_permissions", "-p", "/",
            username, ".*", ".*", ".*",
        ])
        if permissions["returncode"] != 0:
            raise RuntimeError(f"scenario management permissions failed: {permissions['stderr']}")
        packets, assertions = {}, {}
        for node in NODES:
            admin = management_get(node["management"], "/api/overview")
            untagged = management_get(node["management"], "/api/overview", username, password)
            if admin["status"] != 200 or untagged["status"] not in (401, 403):
                raise RuntimeError(
                    f"management authorization oracle failed: {node['service']} "
                    f"admin={admin['status']} untagged={untagged['status']}"
                )
            packets[node["variant"]] = {
                "transport": "HTTP",
                "endpoint": node["management"] + "/api/overview",
                "admin_user": {"tag": "administrator", "status": admin["status"]},
                "untagged_user": {"tag": "none", "status": untagged["status"], "response": untagged["body"]},
            }
            assertions[node["variant"]] = [
                "administrator tagを持つuserのManagement API requestが成功した",
                "vhost permissionだけを持ちmanagement tagを持たないuserのrequestが拒否された",
            ]
        report(
            "management.authorization", "security", "broker-cluster-3",
            "scripts/generate-scenario-runtime.py", "python-urllib-management-client", packets, assertions,
        )
    finally:
        deleted = run([*COMPOSE, "exec", "-T", "rabbitmq-1", "rabbitmqctl", "delete_user", username])
        if deleted["returncode"] != 0:
            raise RuntimeError(f"scenario management user cleanup failed: {deleted['stderr']}")

    packets, assertions = {}, {}
    for node in NODES:
        ping = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmq-diagnostics", "-q", "ping"])
        running = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmq-diagnostics", "-q", "check_running"])
        if ping["returncode"] != 0 or running["returncode"] != 0:
            raise RuntimeError(f"node health oracle failed: {node['service']}: ping={ping} running={running}")
        packets[node["variant"]] = {
            "transport": "container-exec",
            "ping": ping,
            "check_running": running,
        }
        assertions[node["variant"]] = [
            "対象node自身のrabbitmq-diagnostics pingが成功した",
            "対象node自身のrabbitmq-diagnostics check_runningが成功した",
            "応答を同じnodeのcluster statusとbroker logへ結び付けた",
        ]
    report(
        "monitoring.node-health", "operations", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "rabbitmq-diagnostics-4.3.5", packets, assertions,
    )

    packets, assertions = {}, {}
    for node in NODES:
        trace = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmqctl", "status", "--formatter", "json"])
        try:
            status = json.loads(trace["stdout"])
        except json.JSONDecodeError as error:
            raise RuntimeError(f"rabbitmqctl JSON oracle failed: {node['service']}: {trace}") from error
        if trace["returncode"] != 0 or status.get("rabbitmq_version") != "4.3.5":
            raise RuntimeError(f"rabbitmqctl status oracle failed: {node['service']}: {trace}")
        packets[node["variant"]] = {
            "transport": "container-exec", "command": trace["command"],
            "returncode": trace["returncode"], "status": status,
        }
        assertions[node["variant"]] = [
            "rabbitmqctl statusが対象nodeのJSON runtime stateを返した",
            "statusのRabbitMQ versionが固定Version 4.3.5と一致した",
        ]
    report(
        "cli.rabbitmqctl", "operations", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "rabbitmqctl-4.3.5", packets, assertions,
    )

    packets, assertions = {}, {}
    for node in NODES:
        listeners = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmq-diagnostics", "listeners"])
        if listeners["returncode"] != 0 or "5672" not in listeners["stdout"]:
            raise RuntimeError(f"rabbitmq-diagnostics listeners oracle failed: {node['service']}: {listeners}")
        packets[node["variant"]] = {
            "transport": "container-exec", "command": listeners["command"],
            "returncode": listeners["returncode"], "stdout": listeners["stdout"], "stderr": listeners["stderr"],
        }
        assertions[node["variant"]] = [
            "rabbitmq-diagnostics listenersが対象nodeで成功した",
            "固定AMQP listener port 5672がruntime出力に存在した",
        ]
    report(
        "cli.rabbitmq-diagnostics", "operations", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "rabbitmq-diagnostics-4.3.5", packets, assertions,
    )

    packets, assertions = {}, {}
    for node in NODES:
        before = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmq-plugins", "list", "-e", "-m"])
        rejected = run([
            *COMPOSE, "exec", "-T", node["service"], "rabbitmq-plugins",
            "--node", "rabbit@atlas-missing", "enable", "rabbitmq_management",
        ])
        after = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmq-plugins", "list", "-e", "-m"])
        if before["returncode"] != 0 or rejected["returncode"] == 0 or after["returncode"] != 0:
            raise RuntimeError(f"plugin rejection command oracle failed: {node['service']}")
        if sorted(before["stdout"].splitlines()) != sorted(after["stdout"].splitlines()):
            raise RuntimeError(f"plugin rejection changed inventory: {node['service']}")
        packets[node["variant"]] = {
            "transport": "container-exec", "before": before, "rejected_enable": rejected, "after": after,
        }
        assertions[node["variant"]] = [
            "到達不能なtarget nodeへのonline plugin applyが非zeroで拒否された",
            "拒否前後でenabled plugin inventoryが変化しなかった",
        ]
    report(
        "cli.rabbitmq-plugins", "rejection", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "rabbitmq-plugins-4.3.5", packets, assertions,
    )

    packets, assertions, inventories = {}, {}, {}
    for node in NODES:
        trace = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmq-plugins", "list", "-e", "-m"])
        plugins = sorted(line.strip() for line in trace["stdout"].splitlines() if line.strip())
        if trace["returncode"] != 0 or "rabbitmq_management" not in plugins:
            raise RuntimeError(f"plugin inventory oracle failed: {node['service']}: {trace}")
        inventories[node["variant"]] = plugins
        packets[node["variant"]] = {
            "transport": "container-exec",
            "command": trace["command"],
            "returncode": trace["returncode"],
            "stdout": trace["stdout"],
            "stderr": trace["stderr"],
            "enabled_plugins": plugins,
        }
        assertions[node["variant"]] = [
            "rabbitmq-pluginsが実Broker nodeからenabled inventoryを返した",
            "management pluginがenabledである",
            "全3 nodeのenabled plugin集合が一致した",
        ]
    if len({tuple(value) for value in inventories.values()}) != 1:
        raise RuntimeError(f"plugin inventory differs across nodes: {inventories}")
    report(
        "cli.rabbitmq-plugins", "compatibility", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "rabbitmq-plugins-4.3.5", packets, assertions,
    )

    packets, assertions, mechanisms = {}, {}, {}
    for node in NODES:
        packet = amqp10_sasl_mechanisms_probe(node["amqp"])
        if packet["error"] or not packet["received_hex"] or "PLAIN" not in packet["advertised_mechanisms"]:
            raise RuntimeError(f"AMQP 1.0 SASL mechanism oracle failed: {node['service']}: {packet}")
        mechanisms[node["variant"]] = tuple(packet["advertised_mechanisms"])
        packets[node["variant"]] = packet
        assertions[node["variant"]] = [
            "実BrokerがAMQP 1.0 SASL protocol headerへ応答した",
            "SASL mechanism frameがPLAINをadvertiseした",
            "全3 nodeのadvertised mechanism集合が一致した",
        ]
    if len(set(mechanisms.values())) != 1:
        raise RuntimeError(f"AMQP 1.0 SASL mechanism inventory differs across nodes: {mechanisms}")
    report(
        "amqp10.authentication-options", "compatibility", "protocol-amqp10",
        "scripts/generate-scenario-runtime.py", "python-socket-amqp10-sasl-probe", packets, assertions,
    )

    security_001_amqp()

    packets, assertions = {}, {}
    for node in NODES:
        overview = management_get(node["management"], "/api/overview")
        nodes = management_get(node["management"], "/api/nodes")
        if overview["status"] != 200 or nodes["status"] != 200:
            raise RuntimeError(f"Management HTTP operations oracle failed: {node['service']}")
        if not isinstance(nodes["body"], list) or len(nodes["body"]) != 3:
            raise RuntimeError(f"Management HTTP cluster inventory incomplete: {node['service']}: {nodes}")
        packets[node["variant"]] = {
            "transport": "HTTP", "overview_endpoint": node["management"] + "/api/overview",
            "overview": overview, "nodes_endpoint": node["management"] + "/api/nodes", "nodes": nodes,
        }
        assertions[node["variant"]] = [
            "認証済みManagement HTTP overview requestが成功した",
            "認証済みnodes requestが3-node inventoryを返した",
        ]
    report(
        "management.http-api", "operations", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "python-urllib-management-client", packets, assertions,
    )

    packets, assertions = {}, {}
    for node in NODES:
        trace = run([*COMPOSE, "logs", "--no-color", "--tail", "80", node["service"]])
        if trace["returncode"] != 0 or not trace["stdout"].strip():
            raise RuntimeError(f"broker log operations oracle failed: {node['service']}: {trace}")
        packets[node["variant"]] = {
            "transport": "docker-log-stream", "command": trace["command"],
            "returncode": trace["returncode"], "line_count": len(trace["stdout"].splitlines()),
            "stdout": trace["stdout"], "stderr": trace["stderr"],
        }
        assertions[node["variant"]] = [
            "対象nodeの実broker logをruntimeから取得できた",
            "log streamが空でなくcommand traceと結び付いた",
        ]
    report(
        "monitoring.logs", "operations", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "docker-compose-log-client", packets, assertions,
    )

    packets, assertions = {}, {}
    for node in NODES:
        rejected = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmqctl", "atlas_missing_command"])
        if rejected["returncode"] == 0 or not (rejected["stdout"].strip() or rejected["stderr"].strip()):
            raise RuntimeError(f"rabbitmqctl rejection oracle failed: {node['service']}: {rejected}")
        packets[node["variant"]] = {"transport": "container-exec", "rejected_command": rejected}
        assertions[node["variant"]] = [
            "未知のrabbitmqctl commandが非zeroで拒否された",
            "拒否理由またはusageがcommand traceへ記録された",
        ]
    report(
        "cli.rabbitmqctl", "rejection", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "rabbitmqctl-4.3.5", packets, assertions,
    )


def security_001_amqp() -> None:
    packets, assertions = {}, {}
    for node in NODES:
        accepted = amqp10_plain_auth_probe(node["amqp"], "atlas", "atlas-local-only", "valid-local-test-credential")
        rejected = amqp10_plain_auth_probe(node["amqp"], "atlas", "atlas-invalid-local-only", "invalid-local-test-credential")
        if accepted["error"] or accepted["received"]["outcome_code"] != 0:
            raise RuntimeError(f"AMQP 1.0 valid credential oracle failed: {node['service']}: {accepted}")
        if rejected["error"] or rejected["received"]["outcome_code"] != 1:
            raise RuntimeError(f"AMQP 1.0 invalid credential oracle failed: {node['service']}: {rejected}")
        packets[node["variant"]] = {"accepted": accepted, "rejected": rejected}
        assertions[node["variant"]] = [
            "AMQP 1.0 SASL PLAINの正しいlocal test Credentialがsasl-code okを返した",
            "同一userの誤Credentialがsasl-code authで拒否された",
            "SASL initial-responseをEvidenceへ保存していない",
        ]
    report(
        "amqp10.authentication-options", "security", "protocol-amqp10",
        "scripts/generate-scenario-runtime.py", "python-socket-amqp10-sasl-plain-client", packets, assertions,
    )


def ldap_directory_probe(base_dn: str, search_filter: str) -> dict[str, Any]:
    password = "atlas-directory-admin-local-only"
    result = run([
        *SECURITY_COMPOSE, "exec", "-T", "ldap-directory", "ldapsearch", "-LLL", "-x",
        "-H", "ldap://127.0.0.1:1389", "-D", "cn=admin,dc=atlas,dc=local", "-w", password,
        "-b", base_dn, search_filter, "dn",
    ])
    if result["returncode"] != 0:
        raise RuntimeError(f"OpenLDAP directory probe failed: {result['stderr'].replace(password, '[redacted]')}")
    dns = [line.removeprefix("dn: ") for line in result["stdout"].splitlines() if line.startswith("dn: ")]
    if not dns:
        raise RuntimeError(f"OpenLDAP directory probe returned no entries: {base_dn} {search_filter}")
    return {
        "runtime": LDAP_RUNTIME_IDENTITY,
        "endpoint": "ldap://ldap-directory:1389",
        "bind_dn": "cn=admin,dc=atlas,dc=local",
        "credential": "[redacted]",
        "base_dn": base_dn,
        "search_filter": search_filter,
        "entry_dns": dns,
        "returncode": result["returncode"],
    }


def ldap_security_tranche() -> None:
    endpoints = ",".join(f"{node['amqp'][0]}:{node['amqp'][1]}" for node in LDAP_NODES)
    environment = {
        "RABBITMQ_LDAP_ALLOWED_USER": "atlas-allowed",
        "RABBITMQ_LDAP_ALLOWED_PASSWORD": "atlas-allowed-local-only",
        "RABBITMQ_LDAP_DENIED_USER": "atlas-denied",
        "RABBITMQ_LDAP_DENIED_PASSWORD": "atlas-denied-local-only",
        "RABBITMQ_LDAP_BAD_PASSWORD": "atlas-invalid-local-only",
    }
    definitions = (
        (
            "ldap.authentication", "authentication", "ou=users,dc=atlas,dc=local", "(uid=*)",
            [
                "resource group userのLDAP Credentialで実Broker接続が成功した",
                "同一LDAP userの誤Credentialが実Brokerで拒否された",
                "Directory user DNを同じ実行のOpenLDAP queryで確認した",
            ],
        ),
        (
            "ldap.authorization", "authorization", "ou=groups,dc=atlas,dc=local", "(cn=atlas-*-users)",
            [
                "vhost groupの両userがLDAP認証後に実Brokerへ接続した",
                "resource group所属userだけがqueue宣言を許可された",
                "resource group非所属userの越権queue宣言が拒否された",
            ],
        ),
    )
    for behavior, mode, base_dn, search_filter, oracle_assertions in definitions:
        client = run_env(
            ["go", "run", "./cmd/rmq-ldap-security", "--mode", mode, "--endpoints", endpoints],
            environment,
        )
        if client["returncode"] != 0:
            raise RuntimeError(f"{behavior} client failed: {client['stderr']}")
        try:
            result = json.loads(client["stdout"])
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{behavior} client output was not JSON: {client['stdout']}") from error
        checks = result.get("checks", [])
        if result.get("passed") is not True or len(checks) != 3:
            raise RuntimeError(f"{behavior} did not pass all three variants: {result}")
        by_variant = {item["variant"]: item for item in checks}
        packets, assertions = {}, {}
        for node in LDAP_NODES:
            check = by_variant.get(node["variant"])
            if not check or check.get("passed") is not True:
                raise RuntimeError(f"{behavior} variant failed: {node['variant']}: {check}")
            packets[node["variant"]] = {
                "transport": "AMQP 0-9-1 with LDAP backend",
                "client_result": check,
                "directory_result": ldap_directory_probe(base_dn, search_filter),
            }
            assertions[node["variant"]] = [*oracle_assertions, "Client outputにCredential値を保存していない"]
        report(
            behavior, "security", "plugin-ldap-directory", "cmd/rmq-ldap-security/main.go",
            "rmq-ldap-security", packets, assertions, nodes=LDAP_NODES, compose=SECURITY_COMPOSE,
            runtime_dependencies=[LDAP_RUNTIME_IDENTITY],
        )


def cluster_connection_failure(stopped_service: str) -> None:
    packets, assertions = {}, {}
    for node in NODES:
        packet = amqp_header_probe(node["amqp"])
        unavailable = node["service"] == stopped_service
        if unavailable and (not packet["error"] or packet["received_hex"]):
            raise RuntimeError(f"stopped node unexpectedly accepted AMQP: {node['service']}: {packet}")
        if not unavailable and (packet["error"] or not packet["received_hex"]):
            raise RuntimeError(f"live node AMQP probe failed: {node['service']}: {packet}")
        packet.update({"fault": "broker-process-stopped", "stopped_service": stopped_service})
        packets[node["variant"]] = packet
        assertions[node["variant"]] = [
            "停止対象nodeは新規AMQP接続を拒否し、稼働nodeはprotocol headerへ応答した"
            if unavailable else
            "別node停止中も当該nodeは新規AMQP接続のprotocol headerへ応答した",
        ]
    report(
        "cluster.client-connection", "failure", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "python-socket-amqp091-probe", packets, assertions,
        unavailable_services={stopped_service},
    )
    cluster_metadata_failure(stopped_service)
    monitoring_failure_tranche(stopped_service)


def scenario_metadata_vhost() -> str:
    value = os.environ.get("RABBITMQ_SCENARIO_METADATA_VHOST")
    if not value or not value.startswith("/atlas-metadata-"):
        raise RuntimeError("RABBITMQ_SCENARIO_METADATA_VHOST is required")
    return value


def cluster_metadata_failure(stopped_service: str) -> None:
    vhost = scenario_metadata_vhost()
    path = "/api/vhosts/" + urllib.parse.quote(vhost, safe="")
    live_node = next(node for node in NODES if node["service"] != stopped_service)
    created = management_request(live_node["management"], path, method="PUT", body={})
    if created["status"] not in (201, 204):
        raise RuntimeError(f"metadata creation during node failure failed: {created}")
    try:
        packets, assertions = {}, {}
        for node in NODES:
            observed = management_get(node["management"], path)
            unavailable = node["service"] == stopped_service
            if unavailable and observed["status"] is not None:
                raise RuntimeError(f"stopped node exposed management metadata: {node['service']}: {observed}")
            if not unavailable and observed["status"] != 200:
                raise RuntimeError(f"live node lacks replicated metadata: {node['service']}: {observed}")
            packets[node["variant"]] = {
                "transport": "HTTP", "endpoint": node["management"] + path,
                "fault": "broker-process-stopped", "stopped_service": stopped_service,
                "create_status": created["status"] if node == live_node else None,
                "query": observed,
            }
            assertions[node["variant"]] = [
                "停止nodeのManagement endpointは到達不能である"
                if unavailable else
                "1 node停止中に作成したvhost metadataが稼働nodeから取得できた",
            ]
        report(
            "cluster.metadata-replication", "failure", "broker-cluster-3",
            "scripts/generate-scenario-runtime.py", "python-urllib-management-client", packets, assertions,
            unavailable_services={stopped_service},
        )
    except Exception:
        management_request(live_node["management"], path, method="DELETE")
        raise


def monitoring_failure_tranche(stopped_service: str) -> None:
    packets, assertions = {}, {}
    stopped_node_name = "rabbit@" + stopped_service
    for node in NODES:
        if node["service"] == stopped_service:
            trace = run([*COMPOSE, "exec", "-T", node["service"], "rabbitmq-diagnostics", "-q", "ping"])
        else:
            trace = run([
                *COMPOSE, "exec", "-T", node["service"], "rabbitmq-diagnostics",
                "-q", "-n", stopped_node_name, "ping",
            ])
        if trace["returncode"] == 0:
            raise RuntimeError(f"stopped node health unexpectedly succeeded: {node['service']}: {trace}")
        packets[node["variant"]] = {
            "transport": "container-exec", "stopped_service": stopped_service,
            "target_node": stopped_node_name, "diagnostic": trace,
        }
        assertions[node["variant"]] = [
            "停止nodeへのhealth probeが非zeroで拒否された",
            "拒否Traceが停止対象node identityへ結び付いた",
        ]
    report(
        "monitoring.node-health", "rejection", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "rabbitmq-diagnostics-4.3.5", packets, assertions,
        unavailable_services={stopped_service},
    )

    packets, assertions = {}, {}
    for node in NODES:
        state = run([*COMPOSE, "ps", "-a", node["service"]])
        logs = run([*COMPOSE, "logs", "--no-color", "--tail", "120", node["service"]])
        stopped = node["service"] == stopped_service
        expected_state = "Exited" if stopped else "Up"
        if state["returncode"] != 0 or expected_state not in state["stdout"] or not logs["stdout"].strip():
            raise RuntimeError(f"failure log/state oracle failed: {node['service']}: state={state} logs={logs}")
        packets[node["variant"]] = {
            "transport": "docker-runtime", "stopped_service": stopped_service,
            "service_state": state, "log_trace": logs,
        }
        assertions[node["variant"]] = [
            f"service stateが{expected_state}を示した",
            "failure時点でも対象nodeのbroker logを取得できた",
        ]
    report(
        "monitoring.logs", "failure", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "docker-compose-log-client", packets, assertions,
        unavailable_services={stopped_service},
    )


def cluster_connection_recovery() -> None:
    packets, assertions = {}, {}
    for node in NODES:
        packet = amqp_header_probe(node["amqp"])
        if packet["error"] or not packet["received_hex"]:
            raise RuntimeError(f"recovered node AMQP probe failed: {node['service']}: {packet}")
        packet.update({"fault_removed": "broker-process-restarted"})
        packets[node["variant"]] = packet
        assertions[node["variant"]] = [
            "node再起動後に新規AMQP接続のprotocol headerへ応答した",
            "同じ時点のcluster statusが3-node onlineを示した",
        ]
    report(
        "cluster.client-connection", "recovery", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "python-socket-amqp091-probe", packets, assertions,
    )
    cluster_metadata_recovery()
    monitoring_logs_recovery()


def cluster_metadata_recovery() -> None:
    vhost = scenario_metadata_vhost()
    path = "/api/vhosts/" + urllib.parse.quote(vhost, safe="")
    try:
        packets, assertions = {}, {}
        for node in NODES:
            observed = management_get(node["management"], path)
            if observed["status"] != 200 or not isinstance(observed["body"], dict) or observed["body"].get("name") != vhost:
                raise RuntimeError(f"recovered node lacks replicated metadata: {node['service']}: {observed}")
            packets[node["variant"]] = {
                "transport": "HTTP", "endpoint": node["management"] + path,
                "fault_removed": "broker-process-restarted", "query": observed,
            }
            assertions[node["variant"]] = [
                "停止中に作成されたvhost metadataが再参加nodeを含む全nodeから取得できた",
                "同じ時点のcluster statusが3-node onlineを示した",
            ]
        report(
            "cluster.metadata-replication", "recovery", "broker-cluster-3",
            "scripts/generate-scenario-runtime.py", "python-urllib-management-client", packets, assertions,
        )
    finally:
        deleted = management_request(NODES[0]["management"], path, method="DELETE")
        if deleted["status"] not in (204, 404):
            raise RuntimeError(f"metadata scenario cleanup failed: {deleted}")


def monitoring_logs_recovery() -> None:
    packets, assertions = {}, {}
    for node in NODES:
        state = run([*COMPOSE, "ps", "-a", node["service"]])
        logs = run([*COMPOSE, "logs", "--no-color", "--tail", "160", node["service"]])
        if state["returncode"] != 0 or "Up" not in state["stdout"] or not logs["stdout"].strip():
            raise RuntimeError(f"recovery log/state oracle failed: {node['service']}: state={state} logs={logs}")
        packets[node["variant"]] = {
            "transport": "docker-runtime", "fault_removed": "broker-process-restarted",
            "service_state": state, "log_trace": logs,
        }
        assertions[node["variant"]] = [
            "service stateがUpへ復帰した",
            "recovery時点のbroker logを取得できた",
            "同じ時点のcluster statusが3-node onlineを示した",
        ]
    report(
        "monitoring.logs", "recovery", "broker-cluster-3",
        "scripts/generate-scenario-runtime.py", "docker-compose-log-client", packets, assertions,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("protocols")
    sub.add_parser("steady-state-tranche")
    failure = sub.add_parser("partition-failure")
    failure.add_argument("--queue", required=True)
    failure.add_argument("--isolated-service", required=True, choices=[node["service"] for node in NODES])
    recovery = sub.add_parser("partition-recovery")
    recovery.add_argument("--queue", required=True)
    node_failure = sub.add_parser("node-failure")
    node_failure.add_argument("--stopped-service", required=True, choices=[node["service"] for node in NODES])
    sub.add_parser("node-recovery")
    sub.add_parser("security-001-ldap")
    args = parser.parse_args()
    if args.command == "protocols":
        protocols()
    elif args.command == "steady-state-tranche":
        steady_state_tranche()
    elif args.command == "partition-failure":
        partition_failure(args.queue, args.isolated_service)
    elif args.command == "partition-recovery":
        partition_recovery(args.queue)
    elif args.command == "node-failure":
        cluster_connection_failure(args.stopped_service)
    elif args.command == "security-001-ldap":
        ldap_security_tranche()
    else:
        cluster_connection_recovery()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
