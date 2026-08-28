#!/usr/bin/env python3
import argparse
import base64
import datetime
import json
import pathlib
import urllib.parse
import urllib.request


def get_json(url: str, username: str, password: str):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--management-url", required=True)
    parser.add_argument("--username", default="atlas")
    parser.add_argument("--password", default="atlas-local-only")
    parser.add_argument("--expected", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    expected = dict(item.split("=", 1) for item in args.expected)
    base = args.management_url.rstrip("/")
    nodes = get_json(f"{base}/api/nodes", args.username, args.password)
    queues = get_json(f"{base}/api/queues/{urllib.parse.quote('/', safe='')}", args.username, args.password)
    selected_nodes = []
    for node in sorted(nodes, key=lambda item: item.get("name", "")):
        selected_nodes.append({
            "name": node.get("name"),
            "running": node.get("running"),
            "rabbitmq_version": node.get("rabbitmq_version"),
            "erlang_version": node.get("erlang_version"),
            "mem_alarm": node.get("mem_alarm"),
            "disk_free_alarm": node.get("disk_free_alarm"),
            "partitions": node.get("partitions", []),
        })
    selected_queues = []
    for queue in sorted(queues, key=lambda item: item.get("name", "")):
        if not queue.get("name", "").startswith("atlas.upgrade"):
            continue
        selected_queues.append({
            "name": queue.get("name"),
            "type": queue.get("type"),
            "state": queue.get("state"),
            "leader": queue.get("leader"),
            "members": queue.get("members", []),
            "online": queue.get("online", []),
            "messages_ready": queue.get("messages_ready"),
            "messages_unacknowledged": queue.get("messages_unacknowledged"),
        })

    actual_versions = {node["name"]: node["rabbitmq_version"] for node in selected_nodes}
    checks = {
        "three_running_nodes": len(selected_nodes) == 3 and all(node["running"] for node in selected_nodes),
        "expected_versions": actual_versions == expected,
        "no_resource_alarms": all(not node["mem_alarm"] and not node["disk_free_alarm"] for node in selected_nodes),
        "no_partitions": all(not node["partitions"] for node in selected_nodes),
        "upgrade_queues_running": bool(selected_queues) and all(queue["state"] == "running" for queue in selected_queues),
        "quorum_members_online": all(
            queue["type"] != "quorum"
            or (len(queue["members"]) == 3 and sorted(queue["members"]) == sorted(queue["online"]))
            for queue in selected_queues
        ),
    }
    result = {
        "schema_version": 1,
        "phase": args.phase,
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "expected_versions": expected,
        "nodes": selected_nodes,
        "queues": selected_queues,
        "checks": checks,
        "passed": all(checks.values()),
    }
    output = pathlib.Path(args.output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not result["passed"]:
        raise SystemExit(f"snapshot failed: {args.phase}: {checks}")


if __name__ == "__main__":
    main()
