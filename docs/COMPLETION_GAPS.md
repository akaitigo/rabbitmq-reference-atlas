# Completion Boundary

本Repositoryは、`atlas.yaml`と`mastery.yaml`が定めるRabbitMQ 4.3.5の固定Coverage Epochに対し、32 Required TargetをClaim、Version固定Lab、Raw Artifact、Digest付きEvidenceへ接続して閉包しています。

## 閉包済み

- AMQP Model、Exchange/Binding/Queue、Publisher Confirm、Consumer Ack、Redelivery、TTL/DLX
- Quorum/Stream、Consumer/Publisher Flow Control、Ordering、Application IdempotencyとExactly-once非保証の境界
- 三Node Cluster、Leader Failure、実Network Partition、Majority進行、Replica Recovery
- Least-privilege Authorization、Credential Rotation、TLS/mTLSの成功と拒否系
- Management API状態遷移、三Node Prometheus、Health/Alarm、Cleanup Runbook
- 固定WorkloadのClassic/Quorum/Stream性能測定、4.2.9から4.3.5へのRolling Upgrade
- Router Skillと21件のDeterministic Eval

## 境界

AMQP 1.0、MQTT、STOMP、Super Stream、Federation、Shovel、OAuth/LDAP、Kubernetes Operator、Tanzu固有機能は、本Coverage Epochの中では未実証であり、Routerは`gap`として一般化を拒否します。これらを実装済みとは主張しません。

性能値は固定環境の測定であり、本番容量やQueue Typeの普遍的優劣を保証しません。Rolling Upgrade EvidenceはDowngrade、別ClusterへのMessage移送、Exactly-once、Upgrade中の性能保証を含みません。

GitHub Repositoryは公開済みです。Release、OCI Artifact、外部Skill Registryへの公開は行っていません。Completion Certificateは固定ManifestとEvidenceのローカル検証結果であり、GitHub公開の有無や署名済みReleaseのProvenanceを証明するものではありません。
