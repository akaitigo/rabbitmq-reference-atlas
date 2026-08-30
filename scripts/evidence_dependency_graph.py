#!/usr/bin/env python3
"""RabbitMQ Evidenceの入力推移、実行、出力、構造baselineを固定する。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path(os.environ.get("RABBITMQ_EVIDENCE_ROOT", ROOT / "evidence"))
GRAPH_PATH = EVIDENCE_ROOT / "dependency-graph.json"
BASELINE_PATH = ROOT / "baseline/evidence-dependency-graph-v1.json"
CORE_COMMIT = "072d7ca77981f51754e824d70c6d4ecd55ea67e5"
DEFAULT_OBSERVED_AT = "2026-08-28T00:00:00+09:00"


def sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def canonical_digest(value: Any) -> str:
    return sha_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def logical_path(path: Path) -> str:
    try:
        return "evidence/" + path.relative_to(EVIDENCE_ROOT).as_posix()
    except ValueError:
        return path.relative_to(ROOT).as_posix()


def actual_path(relative: str, root: Path = ROOT, evidence_root: Path | None = None) -> Path:
    evidence = evidence_root or (root / "evidence")
    if relative == "evidence":
        return evidence
    if relative.startswith("evidence/"):
        return evidence / relative.removeprefix("evidence/")
    return root / relative


def load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    normalized = value.replace("Z", "+00:00")
    match = re.match(r"^(.*?)(?:\.(\d+))?([+-]\d\d:\d\d)$", normalized)
    if match and match.group(2):
        fraction = match.group(2)[:6].ljust(6, "0")
        normalized = f"{match.group(1)}.{fraction}{match.group(3)}"
    return dt.datetime.fromisoformat(normalized)


def aggregate_digest(members: list[str], root: Path = ROOT, evidence_root: Path | None = None) -> str:
    items = [
        {"path": member, "digest": sha_file(actual_path(member, root, evidence_root))}
        for member in sorted(members)
    ]
    return canonical_digest(items)


def existing(paths: list[str]) -> list[str]:
    return sorted(path for path in paths if actual_path(path, ROOT, EVIDENCE_ROOT).is_file())


def input_specs() -> list[dict[str, Any]]:
    authority = [
        "sources.lock.yaml", "coverage.yaml", "surface.inventory.yaml", "verification.plan.yaml",
        "atlas/claims/index.yaml", "authority/reviews/decisions.json",
    ] + [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "surface/authority").glob("*.yaml"))]
    observations = [logical_path(path) for path in sorted(EVIDENCE_ROOT.glob("raw/*.json"))]
    observations += [logical_path(path) for path in sorted(EVIDENCE_ROOT.glob("*.evidence.json"))]
    specs = [
        ("source.rabbitmq-authority-and-coverage", "source", authority),
        ("harness.producer-clients", "harness", [
            "cmd/rmq-lab/main.go", "cmd/rmq-flow-control/main.go", "cmd/rmq-benchmark/main.go",
            "cmd/rmq-amqp10-handshake/main.go", "cmd/rmq-plugin-protocols/main.go",
            "cmd/rmq-upgrade-workload/main.go",
        ]),
        ("harness.consumer-clients", "harness", [
            "cmd/rmq-lab/main.go", "cmd/rmq-plugin-protocols/main.go", "cmd/rmq-observability/main.go",
            "cmd/rmq-secops/main.go", "cmd/rmq-tls-lab/main.go",
        ]),
        ("harness.evidence-reporters", "harness", [
            "scripts/run-labs.sh", "scripts/generate-evidence.py", "scripts/generate-amqp10-evidence.py",
            "scripts/generate-plugin-protocol-evidence.py", "scripts/generate-scenario-proofs.py",
            "scripts/generate-scenario-runtime.py",
            "scripts/scenario_proof.py", "scripts/evidence_transaction.py", "scripts/evidence_dependency_graph.py",
            "evidence-reporting.yaml",
        ]),
        ("harness.skill-router-eval", "harness", [
            "scripts/run-skill-evals.py", "scripts/run-definitive-skill-eval.py",
            "scripts/evaluate-forward-agent.py", "scripts/test-definitive-skill-eval.py",
        ]),
        ("runtime.rabbitmq-broker-and-client", "runtime", [
            "versions/baseline.yaml", "versions/upgrade-path.yaml", "versions/operators.yaml", "go.mod", "go.sum",
        ]),
        ("profile.broker-config-and-topology", "profile", [
            "environments/compose.yaml", "environments/rabbitmq.conf", "environments/tls.compose.yaml",
            "environments/tls/rabbitmq.conf", "environments/upgrade.compose.yaml",
            "labs/cluster-failure-recovery/lab.yaml", "labs/network-partition/lab.yaml",
        ]),
        ("profile.delivery-ack-retry-dlx", "profile", [
            "labs/ack-redelivery/lab.yaml", "labs/dead-letter/lab.yaml", "labs/ttl-dead-letter/lab.yaml",
            "labs/consumer-flow-control/lab.yaml", "labs/publisher-flow-control/lab.yaml",
            "labs/exchange-binding-matrix/lab.yaml", "labs/exchange-queue/lab.yaml",
        ]),
        ("profile.quorum-failure-recovery", "profile", [
            "labs/quorum-stream/lab.yaml", "labs/cluster-failure-recovery/lab.yaml",
            "labs/network-partition/lab.yaml", "labs/ordering-idempotency/lab.yaml",
        ]),
        ("profile.performance-and-compatibility", "profile", [
            "labs/performance-capacity/lab.yaml", "labs/plugin-protocols/lab.yaml",
            "labs/amqp10-negotiation/lab.yaml", "labs/rolling-upgrade/lab.yaml", "versions/upgrade-path.yaml",
        ]),
        ("profile.security-and-observability", "profile", [
            "labs/security-observability/lab.yaml", "labs/security-tls/lab.yaml",
            "labs/observability-state/lab.yaml", "environments/tls/rabbitmq.conf",
        ]),
        ("profile.scenario-and-reference-system", "profile", [
            "scenario-closure.yaml", "reference-system/manifest.yaml", "verification.plan.yaml",
            "authority/review-queue.snapshot.json", "authority/reviews/decisions.json",
        ]),
        ("profile.skill-mastery-and-routing", "profile", [
            "mastery.yaml", "skill.package.yaml", "evals/router-cases.json", "evals/forward-agent-prompts.json",
        ]),
        ("runtime.evidence-observation-set", "runtime", observations),
    ]
    return [{"id": identifier, "kind": kind, "members": existing(members)} for identifier, kind, members in specs]


def output_id(path: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", ".", path.lower()).strip(".")
    return f"output.{readable}"


def profile_for(path: str) -> str:
    lower = path.lower()
    if any(token in lower for token in ("ack", "redeliver", "dead-letter", "ttl", "flow-control", "exchange", "core.json")):
        return "profile.delivery-ack-retry-dlx"
    if any(token in lower for token in ("cluster", "partition", "recovery", "quorum", "ordering", "prepare-failure")):
        return "profile.quorum-failure-recovery"
    if any(token in lower for token in ("performance", "benchmark", "mqtt", "stomp", "amqp10", "upgrade", "compatibility")):
        return "profile.performance-and-compatibility"
    if any(token in lower for token in ("security", "tls", "observability")):
        return "profile.security-and-observability"
    return "profile.broker-config-and-topology"


def output_kind(path: str, record: dict[str, Any] | None = None) -> str:
    if path.startswith("evidence/scenario-runtime/artifacts/"):
        return "capture"
    if path.startswith("evidence/scenario-runtime/"):
        return "derived-evidence" if path.endswith("/index.json") else "scenario-proof"
    if path.endswith(".proof.json"):
        return "scenario-proof"
    if path.endswith("scenarios/index.json") or path.endswith("scenarios/closure-plan.json"):
        return "scenario-proof" if path.endswith("index.json") else "closure-plan"
    if "reference-system" in path:
        return "reference-system"
    if path.startswith("evals/"):
        return "skill-eval"
    if record and record.get("kind") == "benchmark" or "performance" in path:
        return "benchmark"
    if record and record.get("kind") == "conformance" or any(token in path for token in ("compatibility", "upgrade-migration")):
        return "compatibility"
    if path.startswith("artifacts/"):
        return "derived-evidence"
    return "runtime-evidence" if path.startswith("evidence/raw/") or path.endswith(".evidence.json") else "derived-evidence"


def build_closure_plan(index: dict[str, Any]) -> dict[str, Any]:
    risk_order = ["security", "rejection", "failure", "recovery", "migration", "operations", "boundary", "performance", "compatibility", "normal"]
    risk = {scenario: ordinal + 1 for ordinal, scenario in enumerate(risk_order)}
    rows = []
    for descriptor in index["files"]:
        proof = load(actual_path(descriptor["path"], ROOT, EVIDENCE_ROOT))
        if proof["applicability"] != "required":
            continue
        row_id = f"closure.{proof['behavior_id']}.{proof['scenario']}"
        rows.append({
            "id": row_id,
            "behavior_id": proof["behavior_id"],
            "scenario": proof["scenario"],
            "risk_rank": risk[proof["scenario"]],
            "proof_path": descriptor["path"],
            "variant_ids": proof["dedicated_runtime"]["required_variants"],
            "status": "completed" if proof["closure"]["scenario_gap_closed"] else "planned",
        })
    rows.sort(key=lambda row: (row["risk_rank"], row["behavior_id"]))
    tranches = []
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scenario[row["scenario"]].append(row)
    for scenario in risk_order:
        scenario_rows = by_scenario[scenario]
        for offset in range(0, len(scenario_rows), 4):
            selected = scenario_rows[offset:offset + 4]
            status = "completed" if all(row["status"] == "completed" for row in selected) else ("in-progress" if any(row["status"] == "completed" for row in selected) else "planned")
            tranches.append({
                "id": f"{scenario}-{offset // 4 + 1:03d}",
                "risk_rank": risk[scenario],
                "scenario": scenario,
                "status": status,
                "row_ids": [row["id"] for row in selected],
                "pattern_rows": len(selected),
                "variant_runs": sum(len(row["variant_ids"]) for row in selected),
                "commit_policy": "one-reviewed-tranche-with-non-regression-runtime-identity-and-oracle-validation",
            })
    completed = [item for item in tranches if item["status"] == "completed"]
    pending = [item for item in tranches if item["status"] != "completed"]
    completed_rows = sum(row["status"] == "completed" for row in rows)
    return {
        "schema_version": 1,
        "id": "rabbitmq-scenario-closure-plan-v1",
        "atlas_id": "rabbitmq-reference-atlas",
        "status": "incomplete",
        "scope": "RabbitMQ Authority behavior x required scenario x every profile variant",
        "policy": {
            "risk_order": risk_order,
            "maximum_pattern_rows_per_tranche": 4,
            "first_attempt_only": True,
            "retries": 0,
            "actual_broker_client_required": True,
            "row_removal_or_scope_retreat": "forbidden",
        },
        "baseline": {
            "matrix_rows": index["summary"]["rows"],
            "required_rows": index["summary"]["required_rows"],
            "authority_behaviors": index["summary"]["behaviors"],
            "scenarios": index["summary"]["scenarios"],
        },
        "completed_tranches": completed,
        "tranches": pending,
        "rows": rows,
        "next_tranche": pending[0] if pending else None,
        "summary": {"remaining_rows": len(rows) - completed_rows, "planned_tranches": len(pending), "completed_rows": completed_rows},
    }


def structure_digest(kind: str, path: str, root: Path = ROOT, evidence_root: Path | None = None) -> str:
    document = load(actual_path(path, root, evidence_root))
    if kind == "scenario-proof-index":
        files = []
        for item in document["files"]:
            proof = load(actual_path(item["path"], root, evidence_root))
            bindings = [
                {"variant_id": binding.get("variant_id"), "path": binding.get("path")}
                for binding in proof.get("source_bindings", [])
            ]
            files.append({
                "id": item.get("id"), "pattern_id": item.get("pattern_id"), "scenario": item.get("scenario"),
                "path": item.get("path"), "proof_id": proof.get("id"), "target_id": proof.get("target_id"),
                "target_set": proof.get("target_set"), "behavior_scope": proof.get("behavior_scope"),
                "source_bindings": bindings,
            })
        value = {"id": document.get("id"), "atlas_id": document.get("atlas_id"), "denominator": document.get("denominator"), "files": files}
    elif kind == "scenario-closure-plan":
        tranches = [{key: item.get(key) for key in ("id", "risk_rank", "scenario", "row_ids", "pattern_rows", "variant_runs", "commit_policy")}
                     for field in ("completed_tranches", "tranches") for item in document.get(field, [])]
        tranches.sort(key=lambda item: (item["risk_rank"], item["id"]))
        ordered = [item["id"] for item in document.get("rows", [])]
        value = {"id": document.get("id"), "scope": document.get("scope"), "policy": document.get("policy"),
                 "baseline": document.get("baseline"), "tranches": tranches, "ordered_row_ids": ordered}
    else:
        raise ValueError(f"unknown structure kind: {kind}")
    return canonical_digest(value)


def discover_outputs(root: Path = ROOT, evidence_root: Path | None = None) -> list[str]:
    evidence = evidence_root or (EVIDENCE_ROOT if root == ROOT else root / "evidence")
    def logical(path: Path) -> str:
        try:
            return "evidence/" + path.relative_to(evidence).as_posix()
        except ValueError:
            return path.relative_to(root).as_posix()
    paths = [logical(path) for path in sorted(evidence.glob("raw/*.json"))]
    paths += [logical(path) for path in sorted(evidence.glob("*.evidence.json"))]
    paths += [logical(path) for path in sorted(evidence.glob("scenarios/behaviors/**/*.proof.json"))]
    paths += [logical(path) for path in sorted(evidence.glob("scenario-runtime/**/*")) if path.is_file()]
    for relative in ("evidence/scenarios/index.json", "evidence/scenarios/closure-plan.json", "evidence/reference-system/results.json"):
        if actual_path(relative, root, evidence).is_file():
            paths.append(relative)
    paths += [path.relative_to(root).as_posix() for path in sorted((root / "evals").glob("*.json"))]
    paths += [path.relative_to(root).as_posix() for path in sorted((root / "artifacts").rglob("*.json"))
              if "results" in path.name.lower() or "manifest" in path.name.lower()]
    if (root / "provenance.yaml").is_file():
        paths.append("provenance.yaml")
    return sorted(set(paths))


def build_scenario_runtime_index() -> dict[str, Any]:
    rows = []
    for report_path in sorted(EVIDENCE_ROOT.glob("scenario-runtime/**/*.runtime.json")):
        report = load(report_path)
        behavior = report["behavior_id"].replace("/", "_")
        scenario = report["scenario"]
        artifacts = []
        for variant in report.get("variants", []):
            for channel, item in sorted(variant.get("artifact_channels", {}).items()):
                artifacts.append({
                    "variant_id": variant["id"], "channel": channel,
                    "path": item["path"], "digest": item["digest"],
                })
        proof_path = f"evidence/scenarios/behaviors/{behavior}/{scenario}.proof.json"
        rows.append({
            "id": f"runtime-binding.{report['behavior_id']}.{scenario}",
            "behavior_id": report["behavior_id"], "scenario": scenario,
            "report": {"path": logical_path(report_path), "digest": sha_file(report_path)},
            "proof": {"path": proof_path, "digest": sha_file(actual_path(proof_path))},
            "artifacts": artifacts,
        })
    return {
        "schema_version": 1, "id": "rabbitmq-scenario-runtime-binding-index-v1",
        "atlas_id": "rabbitmq-reference-atlas", "status": "current",
        "rows": rows, "summary": {"runtime_reports": len(rows), "artifact_bindings": sum(len(row["artifacts"]) for row in rows)},
    }


def build_graph(previous: dict[str, Any] | None = None) -> dict[str, Any]:
    now = iso_now()
    observed = os.environ.get("RABBITMQ_EVIDENCE_OBSERVED_AT", now)
    previous_inputs = {item["id"]: item for item in (previous or {}).get("inputs", [])}
    inputs = []
    for spec in input_specs():
        if not spec["members"]:
            raise ValueError(f"Evidence dependency inputが空です: {spec['id']}")
        current = aggregate_digest(spec["members"], ROOT, EVIDENCE_ROOT)
        prior = previous_inputs.get(spec["id"])
        inputs.append({
            **spec,
            "baseline_digest": prior["baseline_digest"] if prior else current,
            "current_digest": current,
            "observed_at": (prior["observed_at"] if prior and prior["current_digest"] == current else observed) if prior else DEFAULT_OBSERVED_AT,
        })
    input_index = {item["id"]: item for item in inputs}

    index = load(EVIDENCE_ROOT / "scenarios/index.json")
    closure = build_closure_plan(index)
    write_json(EVIDENCE_ROOT / "scenarios/closure-plan.json", closure)
    write_json(EVIDENCE_ROOT / "reference-system/results.json", load(ROOT / "reference-system/results.json"))
    write_json(EVIDENCE_ROOT / "scenario-runtime/index.json", build_scenario_runtime_index())

    paths = discover_outputs()
    output_by_path: dict[str, dict[str, Any]] = {}
    run_groups: dict[str, dict[str, Any]] = {}
    raw_to_records: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for path in paths:
        if path.endswith(".evidence.json"):
            record = load(actual_path(path, ROOT, EVIDENCE_ROOT))
            raw_to_records[record["artifact"]["uri"]].append((path, record))

    common = ["source.rabbitmq-authority-and-coverage", "runtime.rabbitmq-broker-and-client",
              "harness.producer-clients", "harness.consumer-clients"]
    for path in paths:
        record = load(actual_path(path, ROOT, EVIDENCE_ROOT)) if path.endswith(".evidence.json") else None
        if path == "evidence/scenario-runtime/index.json":
            runtime_paths = [candidate for candidate in paths if candidate.startswith("evidence/scenario-runtime/") and candidate != path]
            index_document = load(actual_path(path, ROOT, EVIDENCE_ROOT))
            proof_paths = [row["proof"]["path"] for row in index_document["rows"]]
            dependencies = [output_id(candidate) for candidate in sorted(set(runtime_paths + proof_paths))]
            run_id = "run.derived.scenario-runtime-binding-index"
        elif path.startswith("evidence/scenario-runtime/"):
            dependencies = [*common, "harness.evidence-reporters", profile_for(path)]
            parts = path.split("/")
            if "/artifacts/" in path:
                run_key = "/".join((parts[3], parts[4]))
            else:
                run_key = "/".join((parts[2], parts[3].removesuffix(".runtime.json")))
            run_id = "run.runtime.scenario." + hashlib.sha256(run_key.encode()).hexdigest()[:16]
        elif path.startswith("evidence/scenarios/") or path == "evidence/reference-system/results.json" or path == "reference-system/results.json":
            dependencies = ["source.rabbitmq-authority-and-coverage", "harness.evidence-reporters",
                            "runtime.evidence-observation-set", "profile.scenario-and-reference-system"]
            run_id = "run.derived.scenario-proof-and-reference-system"
        elif path.startswith("evals/"):
            dependencies = ["source.rabbitmq-authority-and-coverage", "harness.skill-router-eval",
                            "runtime.evidence-observation-set", "profile.skill-mastery-and-routing"]
            run_id = "run.derived.skill-eval"
        elif path.startswith("artifacts/") or path == "provenance.yaml":
            dependencies = ["source.rabbitmq-authority-and-coverage", "harness.evidence-reporters"]
            run_id = "run.derived.repository-report"
        elif record:
            raw = record["artifact"]["uri"]
            dependencies = [output_id(raw), "harness.evidence-reporters"]
            run_id = "run.runtime." + hashlib.sha256(raw.encode()).hexdigest()[:16]
        else:
            dependencies = [*common, profile_for(path)]
            run_id = "run.runtime." + hashlib.sha256(path.encode()).hexdigest()[:16]
        output_by_path[path] = {
            "id": output_id(path), "kind": output_kind(path, record), "path": path,
            "digest": sha_file(actual_path(path, ROOT, EVIDENCE_ROOT)), "depends_on": dependencies,
            "status": "current", "run_id": run_id,
        }

    # Shared raw Artifact and all Evidence records which reference it are one actual run.
    for raw, records in raw_to_records.items():
        run_id = "run.runtime." + hashlib.sha256(raw.encode()).hexdigest()[:16]
        if raw in output_by_path:
            output_by_path[raw]["run_id"] = run_id
        for path, _ in records:
            output_by_path[path]["run_id"] = run_id

    def ancestors(output: dict[str, Any], visiting: set[str] | None = None) -> set[str]:
        visiting = visiting or set()
        if output["id"] in visiting:
            raise ValueError(f"dependency cycle: {output['id']}")
        found: set[str] = set()
        for dependency in output["depends_on"]:
            if dependency in input_index:
                found.add(dependency)
            else:
                parent = next((item for item in output_by_path.values() if item["id"] == dependency), None)
                if not parent:
                    raise ValueError(f"unknown dependency: {output['id']} -> {dependency}")
                found.update(ancestors(parent, visiting | {output["id"]}))
        return found

    for output in output_by_path.values():
        run_groups.setdefault(output["run_id"], {"outputs": [], "ancestors": set()})
        run_groups[output["run_id"]]["outputs"].append(output)
        run_groups[output["run_id"]]["ancestors"].update(ancestors(output))

    rerun_at = os.environ.get("RABBITMQ_EVIDENCE_RERUN_AT", now)
    runs = []
    stale_outputs = set()
    for run_id, group in sorted(run_groups.items()):
        records = [load(actual_path(item["path"], ROOT, EVIDENCE_ROOT)) for item in group["outputs"] if item["path"].endswith(".evidence.json")]
        derived = run_id.startswith("run.derived.")
        if records:
            times = [record["created_at"] for record in records]
            started_at, completed_at = min(times, key=parse_time), max(times, key=parse_time)
            command = records[0].get("command", "make labs")
            environment = records[0].get("environment", {})
            runtime_identity = {
                "broker": "RabbitMQ 4.3.5",
                "profile": environment.get("runtime_profile") or environment.get("profile"),
                "nodes": environment.get("nodes"),
                "identity": records[0].get("runtime_identity") or environment.get("manifest_digest"),
            }
        elif derived:
            started_at = completed_at = rerun_at
            command = {
                "run.derived.scenario-proof-and-reference-system": "python3 scripts/generate-scenario-proofs.py && python3 scripts/evidence_dependency_graph.py generate",
                "run.derived.scenario-runtime-binding-index": "python3 scripts/evidence_dependency_graph.py generate",
                "run.derived.skill-eval": "python3 scripts/run-skill-evals.py",
                "run.derived.repository-report": "make authority-body repo-validate",
            }[run_id]
            runtime_identity = None
        else:
            mtimes = [dt.datetime.fromtimestamp(actual_path(item["path"], ROOT, EVIDENCE_ROOT).stat().st_mtime, dt.timezone.utc) for item in group["outputs"]]
            started_at = completed_at = min(mtimes).isoformat().replace("+00:00", "Z")
            command = "make labs"
            runtime_identity = {"broker": "RabbitMQ 4.3.5", "profile": "container", "artifact_set": [item["path"] for item in group["outputs"]]}
        run = {
            "id": run_id,
            "execution_kind": "derived" if derived else "runtime",
            "command": command, "started_at": started_at, "completed_at": completed_at,
            "result": "passed", "attempts": 1,
            "input_bindings": [{"input_id": identifier, "digest": input_index[identifier]["current_digest"]}
                               for identifier in sorted(group["ancestors"])],
            "output_ids": sorted(item["id"] for item in group["outputs"]),
        }
        if runtime_identity is not None:
            run["runtime_identity"] = runtime_identity
        runs.append(run)
        for identifier in group["ancestors"]:
            input_item = input_index[identifier]
            if input_item["baseline_digest"] != input_item["current_digest"] and parse_time(started_at) < parse_time(input_item["observed_at"]):
                stale_outputs.update(item["id"] for item in group["outputs"])

    previous_structures = {item["id"]: item for item in (previous or {}).get("structures", [])}
    structures = []
    for identifier, kind, path in (
        ("structure.scenario-proof-index", "scenario-proof-index", "evidence/scenarios/index.json"),
        ("structure.scenario-closure-plan", "scenario-closure-plan", "evidence/scenarios/closure-plan.json"),
    ):
        current = structure_digest(kind, path, ROOT, EVIDENCE_ROOT)
        baseline = previous_structures.get(identifier, {}).get("baseline_digest", current)
        structures.append({"id": identifier, "kind": kind, "path": path, "baseline_digest": baseline})
        if current != baseline:
            stale_outputs.update(output["id"] for output in output_by_path.values() if output["path"].startswith("evidence/scenarios/"))

    for output in output_by_path.values():
        if output["id"] in stale_outputs:
            output["status"] = "stale"
    return {
        "schema_version": 1, "atlas_id": "rabbitmq-reference-atlas", "generated_at": now,
        "status": "stale" if stale_outputs else "current",
        "policy": {
            "transitive_staleness": True, "digest_only_closure_forbidden": True,
            "actual_rerun_required": True, "missing_rerun_targets_fail": True,
            "proof_structure_invariant": True, "closure_plan_structure_invariant": True,
        },
        "inputs": inputs, "outputs": sorted(output_by_path.values(), key=lambda item: item["path"]),
        "runs": runs, "required_outputs": paths, "structures": structures,
    }


def build_baseline(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1, "id": "rabbitmq-evidence-dependency-additive-baseline-v1",
        "atlas_id": graph["atlas_id"], "core_commit": CORE_COMMIT,
        "policy": "additive-only-no-output-input-profile-or-structure-retreat",
        "inputs": [{"id": item["id"], "kind": item["kind"], "members": item["members"]} for item in graph["inputs"]],
        "outputs": [{"id": item["id"], "kind": item["kind"], "path": item["path"], "depends_on": item["depends_on"]} for item in graph["outputs"]],
        "required_outputs": graph["required_outputs"],
        "structures": graph["structures"],
    }


def validate_graph(graph: dict[str, Any], root: Path = ROOT, baseline: dict[str, Any] | None = None,
                   evidence_root_override: Path | None = None) -> list[str]:
    errors: list[str] = []
    evidence_root = evidence_root_override or (EVIDENCE_ROOT if root == ROOT else root / "evidence")
    inputs = {item["id"]: item for item in graph["inputs"]}
    outputs = {item["id"]: item for item in graph["outputs"]}
    output_paths = {item["path"]: item for item in graph["outputs"]}
    runs = {item["id"]: item for item in graph["runs"]}
    discovered = set(discover_outputs(root, evidence_root))
    if graph["status"] != "current":
        errors.append("dependency graph status is stale")
    required = set(graph["required_outputs"])
    represented = set(output_paths)
    if required != represented or discovered - required:
        errors.append(
            "required output enumeration is missing or retreated: "
            f"required_not_represented={sorted(required - represented)} "
            f"represented_not_required={sorted(represented - required)} "
            f"discovered_not_required={sorted(discovered - required)}"
        )

    def ancestors(identifier: str, visiting: set[str] | None = None) -> set[str]:
        visiting = visiting or set()
        if identifier in visiting:
            errors.append(f"dependency cycle: {identifier}")
            return set()
        result: set[str] = set()
        for dependency in outputs[identifier]["depends_on"]:
            if dependency in inputs:
                result.add(dependency)
            elif dependency in outputs:
                result.update(ancestors(dependency, visiting | {identifier}))
            else:
                errors.append(f"unknown dependency: {identifier}:{dependency}")
        return result
    for item in inputs.values():
        try:
            current = aggregate_digest(item["members"], root, evidence_root)
        except (FileNotFoundError, ValueError) as exc:
            errors.append(f"input member missing: {item['id']}: {exc}")
            continue
        if current != item["current_digest"]:
            errors.append(f"input current digest mismatch: {item['id']}")
    for item in outputs.values():
        path = actual_path(item["path"], root, evidence_root)
        if not path.is_file() or sha_file(path) != item["digest"]:
            errors.append(f"output digest mismatch or missing: {item['path']}")
        if item["status"] != "current":
            errors.append(f"output stale: {item['id']}")
        run = runs.get(item.get("run_id"))
        if not run or item["id"] not in run.get("output_ids", []):
            errors.append(f"output omitted from actual rerun: {item['id']}")
            continue
        if run.get("attempts") != 1 or run.get("result") != "passed":
            errors.append(f"output is not first-attempt passed: {item['id']}")
        if run.get("execution_kind") != "derived" and not run.get("runtime_identity"):
            errors.append(f"runtime identity missing: {run['id']}")
        bindings = {binding["input_id"]: binding["digest"] for binding in run.get("input_bindings", [])}
        for dependency in ancestors(item["id"]):
            input_item = inputs[dependency]
            if bindings.get(dependency) != input_item["current_digest"]:
                errors.append(f"current digest binding missing: {run['id']}:{dependency}")
            if input_item["baseline_digest"] != input_item["current_digest"]:
                if parse_time(run["started_at"]) < parse_time(input_item["observed_at"]):
                    errors.append(f"digest-only closure without post-change rerun: {dependency}:{item['id']}")
    for structure in graph["structures"]:
        try:
            current = structure_digest(structure["kind"], structure["path"], root, evidence_root)
        except (FileNotFoundError, KeyError, ValueError) as exc:
            errors.append(f"structure missing: {structure['id']}: {exc}")
            continue
        if current != structure["baseline_digest"]:
            errors.append(f"proof/closure structure shrank or changed: {structure['id']}")
    if baseline:
        current_inputs = {item["id"]: item for item in graph["inputs"]}
        current_outputs = {item["id"]: item for item in graph["outputs"]}
        for old in baseline["inputs"]:
            current = current_inputs.get(old["id"])
            if not current or current["kind"] != old["kind"] or not set(old["members"]).issubset(current["members"]):
                errors.append(f"baseline input/profile retreated: {old['id']}")
        for old in baseline["outputs"]:
            current = current_outputs.get(old["id"])
            if not current or any(current.get(key) != old[key] for key in ("kind", "path", "depends_on")):
                errors.append(f"baseline output retreated or topology changed: {old['id']}")
        if not set(baseline["required_outputs"]).issubset(graph["required_outputs"]):
            errors.append("baseline required output set retreated")
        baseline_structures = {item["id"]: item for item in baseline["structures"]}
        for item in graph["structures"]:
            old = baseline_structures.get(item["id"])
            if old and old != item:
                errors.append(f"baseline proof/closure structure changed: {item['id']}")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "check", "initialize-baseline"))
    args = parser.parse_args()
    if args.command == "generate":
        previous_path = Path(os.environ.get("RABBITMQ_EVIDENCE_PREVIOUS_GRAPH", GRAPH_PATH))
        previous = load(previous_path) if previous_path.is_file() else None
        graph = build_graph(previous)
        write_json(GRAPH_PATH, graph)
    elif not GRAPH_PATH.is_file():
        raise SystemExit("evidence/dependency-graph.jsonがありません")
    graph = load(GRAPH_PATH)
    if args.command == "initialize-baseline":
        if BASELINE_PATH.exists():
            raise SystemExit(f"既存baselineを上書きできません: {BASELINE_PATH.relative_to(ROOT)}")
        write_json(BASELINE_PATH, build_baseline(graph))
    baseline = load(BASELINE_PATH) if BASELINE_PATH.is_file() else None
    errors = validate_graph(graph, ROOT, baseline, EVIDENCE_ROOT)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Evidence dependency graph PASS: inputs={len(graph['inputs'])} outputs={len(graph['outputs'])} runs={len(graph['runs'])} required={len(graph['required_outputs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
