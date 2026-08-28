#!/usr/bin/env python3
import argparse
import json


MODES = {
    "design": [".agents/skills/rabbitmq-reference-router/references/decision-matrix.md", "coverage.yaml"],
    "implement": [".agents/skills/rabbitmq-reference-router/references/capability-index.yaml", "labs/README.md"],
    "diagnose": [".agents/skills/rabbitmq-reference-router/references/runbook-index.yaml", "operations/runbooks/index.yaml"],
    "recover": ["operations/runbooks/cluster-leader-failure.md", "labs/cluster-failure-recovery/lab.yaml"],
    "migrate": [".agents/skills/rabbitmq-reference-router/references/coverage-boundaries.md", "versions/baseline.yaml"],
    "review": [".agents/skills/rabbitmq-reference-router/references/decision-matrix.md", "coverage.yaml"],
    "gap": [".agents/skills/rabbitmq-reference-router/references/coverage-boundaries.md", "coverage.yaml"],
}

GAP = ("mqtt", "stomp", "amqp 1.0", "stream", "super stream", "federation", "shovel", "oauth", "ldap", "kubernetes", "operator", "backup", "performance", "性能", "memory alarm", "disk alarm", "blocked")


def route(query: str) -> dict:
    value = query.casefold()
    if any(keyword in value for keyword in GAP):
        mode, reason, status = "gap", "固定Coverage外のSurfaceです。追加のClaimとEvidenceが必要です。", "outside-current-coverage"
    elif any(keyword in value for keyword in ("復旧", "recover", "leader failure", "leader停止", "node停止", "ノード停止")):
        mode, reason, status = "recover", "Cluster FailureとRecoveryの安全な手順へ案内します。", "candidate-evidence-required"
    elif any(keyword in value for keyword in ("診断", "diagnose", "backlog", "redelivery loop", "再配送ループ", "重複", "dlq backlog")):
        mode, reason, status = "diagnose", "症状をObservable OutcomeとRunbookへ接続します。", "candidate-evidence-required"
    elif any(keyword in value for keyword in ("移行", "migrate", "upgrade", "アップグレード", "mirrored classic")):
        mode, reason, status = "migrate", "固定Versionと未閉包の移行境界を確認します。", "coverage-boundary"
    elif any(keyword in value for keyword in ("review", "レビュー", "lint", "監査")):
        mode, reason, status = "review", "Topologyと保証境界をClaim/Evidenceに照らしてReviewします。", "candidate-evidence-required"
    elif any(keyword in value for keyword in ("実装", "implement", "code", "宣言", "publish", "consume", "ack", "nack", "reject")):
        mode, reason, status = "implement", "最小LabとCapability Indexへ案内します。", "candidate-evidence-required"
    else:
        mode, reason, status = "design", "選択条件と保証境界の判断表へ案内します。", "candidate-evidence-required"
    return {"mode": mode, "baseline": "RabbitMQ 4.3.5", "coverage_status": status, "reason": reason, "references": MODES[mode]}


def main() -> None:
    parser = argparse.ArgumentParser(description="RabbitMQ Reference Atlas Router")
    parser.add_argument("--query", required=True)
    args = parser.parse_args()
    print(json.dumps(route(args.query), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

