#!/usr/bin/env python3
"""RabbitMQ definitive Skill routing contract and evidence bindings."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from collections import Counter, defaultdict
from typing import Any

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
TOKEN = re.compile(r"[a-z0-9][a-z0-9.+_-]*", re.IGNORECASE)

OUTCOME_EXECUTION = {
    "understand": {"mode": "design", "mutation_policy": "read-only", "scenario": "normal"},
    "choose": {"mode": "design", "mutation_policy": "read-only", "scenario": "boundary"},
    "build": {"mode": "implement", "mutation_policy": "explicit-authorization-required", "scenario": "normal"},
    "verify": {"mode": "review", "mutation_policy": "read-only", "scenario": "normal"},
    "operate": {"mode": "diagnose", "mutation_policy": "read-only", "scenario": "operations"},
    "troubleshoot": {"mode": "recover", "mutation_policy": "read-only", "scenario": "failure"},
    "evolve": {"mode": "migrate", "mutation_policy": "explicit-authorization-required", "scenario": "migration"},
    "delegate": {"mode": "review", "mutation_policy": "explicit-authorization-required", "scenario": "normal"},
}

OUTCOME_PROMPTS = {
    "understand": "原理と保証境界を理解したい",
    "choose": "制約とFailure Modelから方式を選びたい",
    "build": "許可された範囲へ構築したい",
    "verify": "主張を実Broker Evidenceで検証したい",
    "operate": "Telemetryから運用状態を判断したい",
    "troubleshoot": "失敗を再現して安全に復旧したい",
    "evolve": "互換性を守って移行したい",
    "delegate": "停止条件付きでAgentへ委任してReviewしたい",
}

STOP_CONDITIONS = [
    "coverage-gap",
    "routing-gap",
    "unverified-evidence",
    "unauthorized-mutation",
    "external-human-authority-decision-required",
    "stale-source-relock-explicit-procedure-required",
    "ambiguous-or-unknown-query",
]


def load_yaml(path: pathlib.Path) -> Any:
    return yaml.safe_load(path.read_text())


def sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    return sha_bytes(path.read_bytes())


def normalized(value: str) -> str:
    return " ".join(TOKEN.findall(value.casefold()))


def tokens(value: str) -> set[str]:
    return {term for term in TOKEN.findall(value.casefold()) if len(term) > 1}


class RoutingContext:
    def __init__(self, root: pathlib.Path = ROOT) -> None:
        self.root = root
        self.mastery = load_yaml(root / "mastery.yaml")
        self.coverage = load_yaml(root / "coverage.yaml")
        self.inventory = load_yaml(root / "surface.inventory.yaml")
        self.plan = load_yaml(root / "verification.plan.yaml")
        self.sources = load_yaml(root / "sources.lock.yaml")
        self.review_queue = json.loads((root / "authority/review-queue.snapshot.json").read_text())
        self.outcomes = {item["id"]: item for item in self.mastery["outcomes"]}
        self.surfaces = {item["id"]: item for item in self.mastery["surfaces"]}
        self.targets = {item["id"]: item for item in self.coverage["targets"]}
        self.items_by_target = {item["target_id"]: item for item in self.inventory["items"]}
        self.rows_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self.plan["rows"]:
            self.rows_by_target[row["target_id"]].append(row)
        self.artifacts = {item["id"]: item for item in self.inventory["authority_artifacts"]}
        self.sources_by_id = {item["id"]: item for item in self.sources["sources"]}
        self.claims = self._load_claims()
        self.evidence = self._load_evidence()
        self.source_lock_digest = sha_file(root / "sources.lock.yaml")

    def _load_claims(self) -> dict[str, dict[str, Any]]:
        claims = {item["id"]: item for item in load_yaml(self.root / "atlas/claims/index.yaml")["claims"]}
        for path in sorted((self.root / "claims").glob("*.claim.yaml")):
            item = load_yaml(path)
            claims[item["id"]] = item
        return claims

    def _load_evidence(self) -> dict[str, tuple[dict[str, Any], pathlib.Path]]:
        result = {}
        for path in sorted((self.root / "evidence").glob("*.evidence.json")):
            item = json.loads(path.read_text())
            result[item["id"]] = (item, path)
        return result


def allowed_target_sets(context: RoutingContext, outcome_id: str, surface_id: str) -> list[str]:
    outcome = context.outcomes[outcome_id]
    surface = context.surfaces[surface_id]
    return sorted(set(outcome["target_sets"]) & set(surface["target_sets"]))


def candidates_for(context: RoutingContext, outcome_id: str, surface_id: str) -> list[dict[str, Any]]:
    allowed = set(allowed_target_sets(context, outcome_id, surface_id))
    if not allowed:
        return []
    exact = [
        context.targets[target_id]
        for target_id, item in context.items_by_target.items()
        if surface_id in item["surface_ids"] and context.targets[target_id]["target_set"] in allowed
    ]
    candidates = exact or [target for target in context.targets.values() if target["target_set"] in allowed]
    return sorted(
        candidates,
        key=lambda target: (
            target["state"] != "covered",
            not bool(target.get("evidence_ids")),
            target["id"],
        ),
    )


def exemplar_target(context: RoutingContext, outcome_id: str, surface_id: str) -> dict[str, Any] | None:
    candidates = candidates_for(context, outcome_id, surface_id)
    return candidates[0] if candidates else None


def rank_target(query: str, candidates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    query_norm = normalized(query)
    query_terms = tokens(query)
    ranked = []
    for target in candidates:
        title_norm = normalized(target["title"])
        target_terms = tokens(f"{target['id']} {target['title']} {target['rationale']}")
        overlap = query_terms & target_terms
        exact = bool(title_norm and title_norm in query_norm)
        score = (1000 if exact else 0) + len(overlap) * 4
        if exact or len(overlap) >= 2:
            ranked.append((score, exact, len(overlap), target["id"], target))
    ranked.sort(key=lambda item: (-item[0], -int(item[1]), -item[2], item[3]))
    if not ranked:
        return None, "unknown-query"
    if sum(1 for item in ranked if item[1]) > 1:
        return None, "ambiguous-query"
    if len(ranked) > 1 and ranked[0][:3] == ranked[1][:3] and not ranked[0][1]:
        return None, "ambiguous-query"
    return ranked[0][4], None


def authority_bindings(context: RoutingContext, target: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    item = context.items_by_target.get(target["id"])
    if item:
        artifact = context.artifacts[item["authority_artifact_id"]]
        source = context.sources_by_id[artifact["source_id"]]
        binding = {
            "binding_type": "primary-authority-locator",
            "source_id": source["id"],
            "url": source["url"],
            "source_digest": source["digest"],
            "authority_artifact_id": artifact["id"],
            "authority_artifact_path": artifact["path"],
            "authority_artifact_digest": artifact["digest"],
            "locator": item["locator"],
            "behavior_id": item["behavior_id"],
        }
        valid = (
            SHA256.fullmatch(source["digest"]) is not None
            and (context.root / artifact["path"]).is_file()
            and sha_file(context.root / artifact["path"]) == artifact["digest"]
        )
        return [binding], "exact-primary-authority" if valid else "invalid-primary-authority-binding"
    return [{
        "binding_type": "authority-lock-manifest",
        "path": "sources.lock.yaml",
        "digest": context.source_lock_digest,
        "note": "既存Targetは個別一次資料locatorへ未接続である。",
    }], "aggregate-lock-only"


def evidence_bindings(context: RoutingContext, evidence_ids: list[str]) -> list[dict[str, Any]]:
    bindings = []
    for evidence_id in sorted(set(evidence_ids)):
        found = context.evidence.get(evidence_id)
        if not found:
            continue
        record, path = found
        bindings.append({
            "id": evidence_id,
            "path": path.relative_to(context.root).as_posix(),
            "digest": sha_file(path),
            "verdict": record.get("verdict"),
            "execution_mode": record.get("execution_mode"),
            "runtime_identity": record.get("runtime_identity"),
            "source_digest": record.get("source_digest"),
            "environment": record.get("environment", {}),
            "artifact": record.get("artifact"),
            "claim_ids": record.get("claim_ids", []),
        })
    return bindings


def variant_binding(context: RoutingContext, target: dict[str, Any], outcome_id: str) -> dict[str, Any]:
    preferred = OUTCOME_EXECUTION[outcome_id]["scenario"]
    rows = [row for row in context.rows_by_target.get(target["id"], []) if row["applicability"] == "required"]
    chosen = next((row for row in rows if row["scenario"] == preferred), rows[0] if rows else None)
    if chosen:
        return {
            "id": f"{target['id']}@{chosen['scenario']}:{chosen['profile']}",
            "scenario": chosen["scenario"],
            "profile": chosen["profile"],
            "execution_requirement": chosen["execution_requirement"],
            "state": chosen["state"],
            "proof_obligation_id": chosen["proof_obligation_id"],
            "evidence_ids": chosen["evidence_ids"],
            "preferred_scenario_match": chosen["scenario"] == preferred,
        }
    evidence = evidence_bindings(context, target.get("evidence_ids", []))
    environment = evidence[0]["environment"] if evidence else {}
    return {
        "id": f"{target['id']}@target-contract",
        "scenario": environment.get("scenario", "target-contract"),
        "profile": environment.get("runtime_profile", environment.get("profile")),
        "execution_requirement": "runtime" if evidence else "unbound",
        "state": target["state"],
        "proof_obligation_id": None,
        "evidence_ids": target.get("evidence_ids", []),
        "preferred_scenario_match": False,
    }


def plan_request(context: RoutingContext, request: dict[str, Any]) -> dict[str, Any]:
    outcome_id = request["outcome"]
    surface_id = request["surface"]
    if outcome_id not in context.outcomes or surface_id not in context.surfaces:
        raise ValueError(f"unknown outcome/surface: {outcome_id}/{surface_id}")
    execution = OUTCOME_EXECUTION[outcome_id]
    allowed = allowed_target_sets(context, outcome_id, surface_id)
    blocks = []
    if execution["mutation_policy"] == "explicit-authorization-required" and request.get("authorized_change") is not True:
        blocks.append("unauthorized-mutation")
    if request.get("authority_semantic_decision") is True:
        blocks.append("external-human-authority-decision-required")
    if request.get("stale_source_relock") is True:
        blocks.append("stale-source-relock-explicit-procedure-required")
    if not allowed:
        return {
            "id": request["id"], "status": "blocked" if blocks else "mastery-routing-gap", "outcome": outcome_id, "surface": surface_id,
            "mode": execution["mode"], "query": request["query"], "target_set_allowed": False,
            "allowed_target_sets": [], "target_binding": None, "variant_binding": None,
            "authority_bindings": [], "authority_binding_status": "not-routable",
            "evidence_bindings": [], "broker_binding": None, "protocol_binding": None,
            "mutation_policy": execution["mutation_policy"], "mutation_status": "blocked" if blocks else "read-only",
            "blocked_reasons": blocks, "stop_conditions": STOP_CONDITIONS,
            "routing_gap": "outcome-surface-target-set-disjoint", "closure_eligible": False,
        }
    target, query_gap = rank_target(request["query"], candidates_for(context, outcome_id, surface_id))
    if target is None:
        return {
            "id": request["id"], "status": "blocked" if blocks else "routing-gap", "outcome": outcome_id, "surface": surface_id,
            "mode": execution["mode"], "query": request["query"], "target_set_allowed": True,
            "allowed_target_sets": allowed, "target_binding": None, "variant_binding": None,
            "authority_bindings": [], "authority_binding_status": "not-routed",
            "evidence_bindings": [], "broker_binding": None, "protocol_binding": None,
            "mutation_policy": execution["mutation_policy"], "mutation_status": "blocked" if blocks else "read-only",
            "blocked_reasons": blocks, "stop_conditions": STOP_CONDITIONS,
            "routing_gap": query_gap, "closure_eligible": False,
        }
    variant = variant_binding(context, target, outcome_id)
    authority, authority_status = authority_bindings(context, target)
    evidence_ids = list(target.get("evidence_ids", [])) + list(variant.get("evidence_ids", []))
    evidence = evidence_bindings(context, evidence_ids)
    runtime = [item for item in evidence if item["execution_mode"] in {"runtime", "platform"} and item["verdict"] == "pass"]
    protocols = sorted({
        item["environment"].get("protocol")
        for item in runtime
        if item["environment"].get("protocol")
    })
    if runtime and not protocols:
        protocols = ["AMQP 0-9-1"]
    broker = {
        "product": "RabbitMQ", "version": "4.3.5", "profile": variant.get("profile"),
        "runtime_proven": bool(runtime), "runtime_identities": sorted({item["runtime_identity"] for item in runtime if item["runtime_identity"]}),
    }
    protocol = {"protocols": protocols, "runtime_proven": bool(protocols and runtime), "evidence_ids": [item["id"] for item in runtime]}
    evidence_closed = bool(runtime) and all(item["source_digest"] == context.source_lock_digest for item in runtime)
    exact_authority = authority_status == "exact-primary-authority"
    closure = (
        not blocks and target["state"] == "covered" and variant["state"] == "covered"
        and exact_authority and evidence_closed and broker["runtime_proven"] and protocol["runtime_proven"]
    )
    gaps = []
    if target["state"] != "covered": gaps.append("target-state-open")
    if variant["state"] != "covered": gaps.append("variant-state-open")
    if not exact_authority: gaps.append("exact-authority-binding-missing")
    if not evidence_closed: gaps.append("runtime-evidence-missing-or-stale")
    if not protocol["runtime_proven"]: gaps.append("protocol-evidence-missing")
    status = "blocked" if blocks else "routed-closed" if closure else "coverage-gap"
    return {
        "id": request["id"], "status": status, "outcome": outcome_id, "surface": surface_id,
        "mode": execution["mode"], "query": request["query"], "target_set_allowed": True,
        "allowed_target_sets": allowed,
        "target_binding": {
            "id": target["id"], "target_set": target["target_set"], "kind": target["kind"],
            "requirement": target["requirement"], "state": target["state"],
            "claim_ids": target["claim_ids"], "evidence_ids": target["evidence_ids"],
        },
        "variant_binding": variant, "authority_bindings": authority,
        "authority_binding_status": authority_status, "evidence_bindings": evidence,
        "broker_binding": broker, "protocol_binding": protocol,
        "mutation_policy": execution["mutation_policy"],
        "mutation_status": "read-only" if execution["mutation_policy"] == "read-only" else "blocked" if blocks else "authorized-for-request-scope",
        "blocked_reasons": blocks, "stop_conditions": STOP_CONDITIONS,
        "routing_gap": None, "closure_gaps": gaps, "closure_eligible": closure,
    }


def matrix_requests(context: RoutingContext) -> list[tuple[dict[str, Any], str | None]]:
    requests = []
    for outcome in context.mastery["outcomes"]:
        for surface in context.mastery["surfaces"]:
            target = exemplar_target(context, outcome["id"], surface["id"])
            query = (
                f"RabbitMQ 4.3.5の{target['title']}について{OUTCOME_PROMPTS[outcome['id']]}"
                if target else f"RabbitMQ 4.3.5の{surface['title']}について{OUTCOME_PROMPTS[outcome['id']]}"
            )
            requests.append(({
                "id": f"skill.{outcome['id']}.{surface['id']}", "outcome": outcome["id"],
                "surface": surface["id"], "query": query,
                "authorized_change": OUTCOME_EXECUTION[outcome["id"]]["mutation_policy"] == "explicit-authorization-required",
            }, target["id"] if target else None))
    return requests


def evaluate_matrix_plan(plan: dict[str, Any], expected_target: str | None) -> dict[str, bool]:
    expected_gap = expected_target is None
    return {
        "identity": plan["id"] == f"skill.{plan['outcome']}.{plan['surface']}",
        "mode": plan["mode"] == OUTCOME_EXECUTION[plan["outcome"]]["mode"],
        "target_or_declared_gap": (
            plan["target_binding"] is not None and plan["target_binding"]["id"] == expected_target
        ) if not expected_gap else plan["routing_gap"] == "outcome-surface-target-set-disjoint",
        "mutation_authorization": plan["mutation_status"] in {"read-only", "authorized-for-request-scope"},
        "gap_honesty": plan["closure_eligible"] or plan["status"] in {"coverage-gap", "mastery-routing-gap"},
        "binding_shapes": all(
            SHA256.fullmatch(binding.get("source_digest", binding.get("digest", ""))) is not None
            for binding in plan["authority_bindings"]
        ) if plan["authority_bindings"] else expected_gap,
        "stop_conditions": all(item in plan["stop_conditions"] for item in STOP_CONDITIONS),
    }


def target_state_inventory(context: RoutingContext) -> dict[str, Any]:
    targets = [{
        "id": target["id"], "target_set": target["target_set"], "requirement": target["requirement"],
        "state": target["state"], "claim_ids": target["claim_ids"], "evidence_ids": target["evidence_ids"],
    } for target in context.coverage["targets"]]
    counts = Counter(item["state"] for item in targets)
    return {
        "total": len(targets), "state_counts": dict(sorted(counts.items())),
        "all_required_covered": all(item["state"] == "covered" for item in targets if item["requirement"] == "required"),
        "targets": targets,
    }
