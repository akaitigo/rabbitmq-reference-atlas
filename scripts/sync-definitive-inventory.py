#!/usr/bin/env python3
"""Authority Artifactから細粒度Coverage TargetとSurface Inventoryを同期する。"""

import hashlib
import pathlib

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTHORITY_DIR = ROOT / "surface/authority"

AGGREGATE_TARGETS = {
    "protocol.amqp10-native", "protocol.mqtt-native", "protocol.stomp-plugin",
    "protocol.stream-binary", "protocol.websocket-transports", "client.connection-recovery",
    "management.http-api", "management.cli-toolchain", "configuration.precedence-reload",
    "configuration.policies-parameters", "migration.feature-flag-lifecycle",
    "queue.classic-semantics", "queue.quorum-semantics-definitive", "stream.retention-replay",
    "stream.super-stream", "routing.exchange-binding-policy", "delivery.unroutable-return-confirm",
    "consumer.lifecycle-priority-cancel", "crosscluster.federation", "crosscluster.shovel",
    "security.auth-backend-chain", "security.oauth2-jwt", "security.ldap",
    "security.tls-cluster-and-clients", "observability.full-stack",
    "capacity.memory-disk-alarms", "performance.comparison-variants",
    "recovery.backup-restore", "migration.rollback-boundary",
    "operator.cluster-reconciliation", "operator.topology-reconciliation",
    "system.integrated-reference", "skill.definitive-evaluation",
    "assurance.definitive-certificate",
}


def sha(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def target_set_for(behavior_id: str) -> str:
    prefix = behavior_id.split(".", 1)[0]
    return {
        "protocol": "protocols-client-contracts",
        "amqp10": "protocols-client-contracts",
        "mqtt": "protocols-client-contracts",
        "stomp": "protocols-client-contracts",
        "stream-plugin": "protocols-client-contracts",
        "stream": "messaging-surface",
        "exchange": "messaging-surface",
        "queue": "messaging-surface",
        "classic-queue": "messaging-surface",
        "delivery": "delivery-pressure",
        "publisher": "delivery-pressure",
        "consumer": "delivery-pressure",
        "client": "protocols-client-contracts",
        "quorum": "cluster-resilience",
        "cluster": "cluster-resilience",
        "partition": "cluster-resilience",
        "management": "control-plane-configuration",
        "cli": "control-plane-configuration",
        "configuration": "control-plane-configuration",
        "policy": "control-plane-configuration",
        "plugin": "control-plane-configuration",
        "feature-flags": "migration-evolution",
        "upgrade": "migration-evolution",
        "federation": "cross-cluster-messaging",
        "shovel": "cross-cluster-messaging",
        "security": "security-safety",
        "oauth2": "security-safety",
        "ldap": "security-safety",
        "tls": "security-safety",
        "backup": "data-protection",
        "operator": "operator-platform",
        "operator-usage": "operator-platform",
        "topology-operator": "operator-platform",
        "monitoring": "operations-observability",
        "prometheus": "operations-observability",
        "alarm": "performance-capacity",
        "limit": "performance-capacity",
        "production": "integrated-reference-system",
    }[prefix]


def kind_for(behavior_id: str) -> str:
    prefix = behavior_id.split(".", 1)[0]
    if prefix in {"security", "oauth2", "ldap", "tls"}:
        return "security"
    if prefix in {"feature-flags", "upgrade"}:
        return "migration"
    if prefix in {"alarm", "limit"}:
        return "performance"
    if prefix in {"management", "cli", "configuration", "policy", "plugin", "operator", "operator-usage", "topology-operator", "monitoring", "prometheus"}:
        return "operation"
    if prefix in {"backup", "federation", "shovel"}:
        return "failure"
    if prefix == "production":
        return "construction"
    if prefix in {"protocol", "amqp10", "mqtt", "stomp", "stream-plugin"}:
        return "compatibility"
    return "capability"


def runtime_profile_for(item: dict) -> str:
    """Behaviorの実行母集団をProtocol/Plugin/Platform境界ごとに分離する。"""
    capability = item["capability_id"]
    prefix = item["behavior_id"].split(".", 1)[0]
    if prefix in {"operator", "operator-usage", "topology-operator"}:
        return "kubernetes-operator"
    if capability == "protocol.amqp10" or prefix == "amqp10":
        return "protocol-amqp10"
    if capability.startswith("protocol.mqtt") or capability.startswith("security.mqtt") or prefix == "mqtt":
        return "plugin-mqtt"
    if capability.startswith("protocol.stomp") or capability.startswith("security.stomp") or capability.startswith("delivery.stomp") or prefix == "stomp":
        return "plugin-stomp"
    if capability.startswith("protocol.stream") or capability.startswith("security.stream") or capability.startswith("delivery.stream") or capability.startswith("observability.stream") or prefix == "stream-plugin":
        return "plugin-stream"
    if capability == "crosscluster.federation" or prefix == "federation":
        return "cross-cluster-federation"
    if capability == "crosscluster.shovel" or prefix == "shovel":
        return "cross-cluster-shovel"
    if capability == "security.oauth2" or prefix == "oauth2":
        return "plugin-oauth2-idp"
    if capability == "security.ldap" or prefix == "ldap":
        return "plugin-ldap-directory"
    if capability == "security.tls" or prefix == "tls":
        return "tls-cluster-client"
    if capability == "migration.upgrade" or prefix == "upgrade":
        return "rolling-upgrade"
    if capability.startswith("capacity.") or item["behavior_id"].startswith(("alarm.", "limit.")):
        return "capacity-benchmark"
    if capability == "system.reference" or prefix == "production":
        return "integrated-reference-system"
    if capability in {"protocol.amqp091", "protocol.websocket", "protocol.http"} or prefix == "protocol":
        return "protocol-amqp091-control-plane"
    return "broker-cluster-3"


def main() -> None:
    coverage_path = ROOT / "coverage.yaml"
    coverage = yaml.safe_load(coverage_path.read_text())
    sources = yaml.safe_load((ROOT / "sources.lock.yaml").read_text())
    existing_targets = {item["id"]: item for item in coverage["targets"]}
    existing_claim_status = {}
    claim_dir = ROOT / "claims"
    if claim_dir.exists():
        for claim_path in claim_dir.glob("definitive.*.claim.yaml"):
            claim = yaml.safe_load(claim_path.read_text())
            existing_claim_status[claim["id"]] = claim["status"]
    existing_plan_rows = {}
    plan_path = ROOT / "verification.plan.yaml"
    if plan_path.exists():
        old_plan = yaml.safe_load(plan_path.read_text())
        existing_plan_rows = {(row["behavior_id"], row["scenario"]): row for row in old_plan.get("rows", [])}
    retained = [
        item for item in coverage["targets"]
        if item["id"] not in AGGREGATE_TARGETS and not item["id"].startswith("definitive.")
    ]

    artifacts = []
    inventory_items = []
    source_by_artifact = {}
    generated_targets = []
    seen_behaviors = set()
    for path in sorted(AUTHORITY_DIR.glob("*.authority-surfaces.yaml")):
        document = yaml.safe_load(path.read_text())
        artifact_id = path.name.removesuffix(".authority-surfaces.yaml").replace(".", "-")
        relative = path.relative_to(ROOT).as_posix()
        artifacts.append({
            "id": artifact_id,
            "source_id": document["source_id"],
            "path": relative,
            "digest": sha(path),
        })
        source_by_artifact[artifact_id] = document["source_id"]
        for surface in document["surfaces"]:
            behavior_id = surface["behavior_id"]
            if behavior_id in seen_behaviors:
                raise SystemExit(f"duplicate behavior_id: {behavior_id}")
            seen_behaviors.add(behavior_id)
            target_id = f"definitive.{behavior_id}"
            claim_id = f"definitive.{behavior_id}.claim"
            inventory_items.append({
                "id": f"inventory.{behavior_id}",
                "authority_artifact_id": artifact_id,
                "authority_surface_id": surface["id"],
                "locator": surface["locator"],
                "kind": surface["kind"],
                "capability_id": surface["capability_id"],
                "behavior_id": behavior_id,
                "target_id": target_id,
                "title": surface["title"],
                "surface_ids": surface["surface_ids"],
                "classification": "included",
                "rationale": "RabbitMQ公式一次資料が明示する独立Behaviorであり、集約せず専用TargetとClaimでClosureを証明する。",
                "claim_ids": [claim_id],
            })
            previous_target = existing_targets.get(target_id, {})
            generated_targets.append({
                "id": target_id,
                "title": surface["title"],
                "target_set": target_set_for(behavior_id),
                "kind": kind_for(behavior_id),
                "requirement": "required",
                "state": previous_target.get("state", "planned"),
                "rationale": "Authorityから抽出したBehaviorを正常・境界・拒否と適用Surface固有のScenarioで個別に証明する。",
                "claim_ids": [claim_id],
                "evidence_ids": previous_target.get("evidence_ids", []),
            })

    coverage["targets"] = retained + generated_targets
    coverage_path.write_text(yaml.safe_dump(coverage, allow_unicode=True, sort_keys=False))
    inventory = {
        "schema_version": 2,
        "atlas_id": coverage["atlas_id"],
        "epoch": coverage["epoch"],
        "authority_lock_digest": coverage["authority_lock_digest"],
        "authority_artifacts": artifacts,
        "items": inventory_items,
    }
    (ROOT / "surface.inventory.yaml").write_text(yaml.safe_dump(inventory, allow_unicode=True, sort_keys=False))
    artifact_by_source = {artifact["source_id"]: artifact["id"] for artifact in artifacts}
    source_classification = {
        "schema_version": 1,
        "atlas_id": coverage["atlas_id"],
        "authority_lock_digest": coverage["authority_lock_digest"],
        "sources": [
            {
                "source_id": source["id"],
                "classification": "surface-authority" if source["id"] in artifact_by_source else "supporting-authority",
                "authority_artifact_id": artifact_by_source.get(source["id"]),
                "rationale": "Authority Surfaceを抽出した一次資料。" if source["id"] in artifact_by_source else "Version、Runtime、補完的契約の固定に使用し、独自Surfaceの抽出元にはしない。",
            }
            for source in sources["sources"]
        ],
    }
    (ROOT / "surface/source-classification.yaml").write_text(yaml.safe_dump(source_classification, allow_unicode=True, sort_keys=False))
    scenario_by_surface = {
        "failure-recovery": {"failure", "recovery"},
        "operations-observability": {"operations"},
        "security-privacy-safety": {"security"},
        "performance-capacity-cost": {"performance"},
        "compatibility-integration": {"compatibility"},
        "migration-evolution-deprecation": {"migration"},
    }
    scenarios = ["normal", "boundary", "rejection", "failure", "recovery", "migration", "operations", "security", "performance", "compatibility"]
    plan_rows = []
    for item in inventory_items:
        required = {"normal", "boundary", "rejection"}
        for surface_id in item["surface_ids"]:
            required.update(scenario_by_surface.get(surface_id, set()))
        for scenario in scenarios:
            applies = scenario in required
            proof_id = f"proof.{item['behavior_id']}.{scenario}" if applies else None
            previous_row = existing_plan_rows.get((item["behavior_id"], scenario), {})
            plan_rows.append({
                "behavior_id": item["behavior_id"],
                "target_id": item["target_id"],
                "scenario": scenario,
                "applicability": "required" if applies else "not-applicable",
                "state": previous_row.get("state", "planned") if applies else "not-applicable",
                "execution_requirement": "platform" if item["behavior_id"].split(".", 1)[0] in {"operator", "operator-usage", "topology-operator"} and applies else ("runtime" if applies else "not-applicable"),
                "profile": runtime_profile_for(item) if applies else None,
                "proof_obligation_id": proof_id,
                "evidence_ids": previous_row.get("evidence_ids", []) if applies else [],
                "artifact_required": applies,
                "rationale": "Mastery Surfaceから必須となる専用Scenarioで、実Runtimeと専用Artifact Evidenceで閉じる。" if applies else "当該BehaviorのMastery SurfaceからこのScenarioは導出されないため適用しない。",
            })
    plan = {
        "schema_version": 1,
        "atlas_id": coverage["atlas_id"],
        "epoch": coverage["epoch"],
        "status": "incomplete",
        "core_target": "verification.matrix.yaml (Subject Definitive Gate v2)",
        "runtime_contracts": [
            {"id": "broker-cluster-3", "minimum_nodes": 3, "actual_runtime": True, "required_for": ["amqp091", "queues", "delivery", "cluster", "partition", "operations"]},
            {"id": "protocol-amqp10", "minimum_nodes": 3, "actual_runtime": True, "required_for": ["amqp10"]},
            {"id": "protocol-amqp091-control-plane", "minimum_nodes": 3, "actual_runtime": True, "required_for": ["amqp091", "websocket", "http"]},
            {"id": "plugin-mqtt", "minimum_nodes": 3, "actual_runtime": True, "required_for": ["mqtt"]},
            {"id": "plugin-stomp", "minimum_nodes": 3, "actual_runtime": True, "required_for": ["stomp"]},
            {"id": "plugin-stream", "minimum_nodes": 3, "actual_runtime": True, "required_for": ["stream-protocol", "super-stream"]},
            {"id": "cross-cluster-federation", "minimum_clusters": 2, "minimum_total_nodes": 6, "actual_runtime": True, "required_for": ["federation"]},
            {"id": "cross-cluster-shovel", "minimum_clusters": 2, "minimum_total_nodes": 6, "actual_runtime": True, "required_for": ["shovel"]},
            {"id": "plugin-oauth2-idp", "minimum_nodes": 3, "external_services": ["oidc-provider"], "actual_runtime": True, "required_for": ["oauth2"]},
            {"id": "plugin-ldap-directory", "minimum_nodes": 3, "external_services": ["ldap-directory"], "actual_runtime": True, "required_for": ["ldap"]},
            {"id": "tls-cluster-client", "minimum_nodes": 3, "actual_runtime": True, "required_for": ["tls", "mtls", "certificate-rotation"]},
            {"id": "rolling-upgrade", "minimum_nodes": 3, "minimum_versions": 2, "actual_runtime": True, "required_for": ["upgrade", "rollback-boundary"]},
            {"id": "capacity-benchmark", "minimum_nodes": 3, "raw_samples": True, "actual_runtime": True, "required_for": ["alarms", "limits", "performance"]},
            {"id": "kubernetes-operator", "minimum_broker_replicas": 3, "actual_platform": True, "required_for": ["cluster-operator", "topology-operator"]},
            {"id": "integrated-reference-system", "minimum_nodes": 3, "actual_runtime": True, "required_for": ["cross-behavior-normal", "failure", "recovery", "operations"]},
        ],
        "global_closure_requirements": [
            "network partitionと切断後の復旧を専用Artifactで証明する",
            "rolling upgradeと許可されるrollback境界を継続Workload下で証明する",
            "decision-comparison Surfaceは二つ以上のVariantと専用Evidenceを持つ",
            "performance SurfaceはRaw Sample、容量条件、分位、Resource Snapshotを持つ",
            "統合Reference Systemは複数Behaviorの正常・障害・回復をEnd-to-Endで証明する",
            "8 Outcomeと14 Surface、Gap応答、Authorization境界をDefinitive Skill Evalで閉じる",
        ],
        "rows": plan_rows,
    }
    (ROOT / "verification.plan.yaml").write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False))
    claim_dir.mkdir(exist_ok=True)
    for stale in claim_dir.glob("definitive.*.claim.yaml"):
        stale.unlink()
    criteria = {
        "normal": ["固定Versionの実Runtimeで正常入力を実行し、期待Outcomeを構造化Artifactで判定する。"],
        "boundary": ["上限、下限、空、重複または優先順位の境界を一つずつ変え、反証可能なOracleで判定する。"],
        "rejection": ["無効な設定、Credential、Protocolまたは操作を与え、部分成功を認めず明示的な拒否を確認する。"],
        "failure": ["専用Environment内だけに停止、Network断、Resource Alarmまたは依存先障害を注入し、保証Invariantを検証する。"],
        "recovery": ["障害要因を取り除き、Membership、Delivery、Telemetryとデータ整合性が制限時間内に回復することを確認する。"],
        "migration": ["固定したSourceとTarget Versionの間で継続Workloadを実行し、互換性とRollback不能境界を記録する。"],
        "operations": ["Metrics、Logs、Health、CLI状態を同一Run IDと時刻で相関し、診断から安全な復旧判定まで接続する。"],
        "security": ["最小権限の許可と、誤Credential、不正なTrust、越権操作の拒否を同一隔離Environmentで検証する。"],
        "performance": ["条件を固定した複数VariantでRaw Sample、分位、Rate、Memory、Diskを取得し、適用範囲外へ一般化しない。"],
        "compatibility": ["二つ以上のProtocol、Client、Queue、PluginまたはVersion Variantを同一Oracleで比較する。"],
    }
    criteria_overrides = {
        ("amqp10.version-negotiation", "security"): [
            "SASLなしのAMQP 1.0 Headerを送信し、Brokerが接続継続や匿名受理をせず、SASL 1.0 Headerを要求することを全三Nodeで確認する。"
        ],
    }
    rows_by_behavior = {}
    for row in plan_rows:
        if row["applicability"] == "required":
            rows_by_behavior.setdefault(row["behavior_id"], []).append(row)
    for item in inventory_items:
        claim_id = item["claim_ids"][0]
        proof_obligations = []
        for row in rows_by_behavior[item["behavior_id"]]:
            scenario = row["scenario"]
            proof_obligations.append({
                "id": row["proof_obligation_id"],
                "statement": f"{item['title']}の{scenario} Scenarioを専用実Runtime Evidenceで証明する。",
                "acceptance_criteria": criteria_overrides.get((item["behavior_id"], scenario), criteria[scenario]),
            })
        claim = {
            "schema_version": 1,
            "id": claim_id,
            "atlas_id": coverage["atlas_id"],
            "capability_id": item["capability_id"],
            "statement": f"{item['title']}のRabbitMQ 4.3.5における公開契約は、Authorityから導出した各必須Scenarioの実行結果と一致する。",
            "status": existing_claim_status.get(claim_id, "proposed"),
            "source_ids": [source_by_artifact[item["authority_artifact_id"]]],
            "proof_obligations": proof_obligations,
        }
        filename = f"{item['target_id']}.claim.yaml"
        (claim_dir / filename).write_text(yaml.safe_dump(claim, allow_unicode=True, sort_keys=False))
    print(f"definitive inventory synced: artifacts={len(artifacts)} behaviors={len(inventory_items)} targets={len(coverage['targets'])}")


if __name__ == "__main__":
    main()
