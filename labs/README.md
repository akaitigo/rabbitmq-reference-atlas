# Labs

すべてのLabは次の共通Contractを持ちます。

1. Setup: 固定Digestの三ノードRabbitMQ Clusterを専用Compose projectへ作成する。
2. Execute: Lab固有のTopologyとMessageを一意Namespaceで作成し、操作する。
3. Verify: 文言ではなくPublisher Confirm、Delivery Flag、Header、Message ID、Cluster MembershipをOracleにする。
4. Cleanup: Lab固有Resourceを削除し、最後にCompose projectとVolumeを削除する。

```bash
make labs
```

`evidence/raw/`はHarnessの直接出力、`evidence/*.evidence.json`はCore Evidence Schemaへ変換したRecordです。GeneratorはEvidence IDごとの必須Check名と`passed=true`を検査し、失敗または欠落したRawから`verdict: pass`を生成しません。Cluster Failureは対象Quorum QueueのLeaderをManagement APIから特定し、その一Nodeだけを停止します。Network Partitionは別Labとして専用Compose NetworkからLeader Containerを隔離し、Trapで必ず再接続します。

`delivery.flow-control`はConsumer Prefetchの配送制御、`delivery.publisher-flow-control`はMemory Alarm中のConnection.Blockedと解除後のConfirm再開を対象にします。両者は別Claim・別Evidenceであり、同じ保証として扱いません。
