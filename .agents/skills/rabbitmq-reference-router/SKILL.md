---
name: rabbitmq-reference-router
description: RabbitMQ 4.3.5のAMQP Model、Topology、Delivery、Quorum/Stream、Flow Control、Cluster/Partition/Recovery、Security、Observability、Performance、Upgrade/Migrationについて、設計・実装・診断・復旧・移行・Reviewを固定Coverage、Lab、Evidenceへ案内する。RabbitMQ以外のBroker比較や未検証Protocolを実証済みとして扱う依頼には使用しない。
---

# RabbitMQ Reference Router

このSkillは技術知識の別正本ではありません。Repository Rootの`coverage.yaml`、`atlas/claims/index.yaml`、`labs/`、`operations/runbooks/`、`evidence/`へRouteし、固定Versionで立証済みの範囲だけを回答します。

## Route

1. `python3 .agents/skills/rabbitmq-reference-router/scripts/route.py --outcome '<Outcome>' --surface '<Surface>' --query '<依頼>'`でTarget、検証Variant、Authority、実Broker/Protocol Evidenceを選びます。変更を伴う`build`、`evolve`、`delegate`では明示的な許可がある場合だけ`--authorized-change`を付けます。
2. Modeに応じて次だけを読みます。
   - `design`または`review`: [references/decision-matrix.md](references/decision-matrix.md)
   - `implement`: [references/capability-index.yaml](references/capability-index.yaml)と対象`labs/*/lab.yaml`
   - `diagnose`または`recover`: [references/runbook-index.yaml](references/runbook-index.yaml)と対象Runbook
   - `migrate`または`gap`: [references/coverage-boundaries.md](references/coverage-boundaries.md)
3. 実行を伴う主張では、対象Claimの`evidence_ids`とEvidence Artifactを確認します。Evidenceがない、失効している、または`state`が`partial`なら、検証済みとは表現しません。

## 境界

- Canonical Lab ProtocolはAMQP 0-9-1です。AMQP 1.0、Stream Protocol、MQTT、STOMPの挙動へ一般化しません。
- Consumer Prefetch、Connection State `flow`、resource alarmによる`blocked`を同じ機能として扱いません。
- Consumer AcknowledgementとPublisher Confirmを同じ保証として扱いません。
- Node停止とNetwork Partitionを同じFailureとして扱いません。
- RabbitMQの配送をExactly-onceや組込み重複排除として扱わず、Application Idempotency境界を示します。
- 固定性能測定をProduction CapacityやQueue Typeの普遍的優劣へ一般化しません。
- Failure Labは専用Compose projectだけを操作します。外部Clusterを停止しません。
- Coverage外では既存Capabilityを捏造せず、`gap`と必要な追加Evidenceを返します。
- ユーザーが実装・変更・公開を依頼していない場合、診断と根拠提示に留めます。
- 曖昧または未知のQueryはTargetを推測せず`routing-gap`として停止します。
- `pending-human`のAuthority判断はAgentが昇格せず、`--authority-semantic-decision`では停止します。
- stale Sourceのrelockは明示手順なしに進めず、`--stale-source-relock`では停止します。

## 出力

回答には、選択したMode、固定Version、Target state、Variant、Authority locator、Broker/Protocol Evidenceの有無、適用条件、保証しない境界を短く含めます。実行した場合は再現コマンドとObservable Outcomeを示します。Router matrixの通過だけをRepository完成判定に使用しません。
