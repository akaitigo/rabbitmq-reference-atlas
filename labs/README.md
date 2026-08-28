# Labs

すべてのLabは次の共通Contractを持ちます。

1. Setup: 固定Digestの三ノードRabbitMQ Clusterを専用Compose projectへ作成する。
2. Execute: Lab固有のTopologyとMessageを一意Namespaceで作成し、操作する。
3. Verify: 文言ではなくPublisher Confirm、Delivery Flag、Header、Message ID、Cluster MembershipをOracleにする。
4. Cleanup: Lab固有Resourceを削除し、最後にCompose projectとVolumeを削除する。

```bash
make labs
```

`evidence/raw/`はHarnessの直接出力、`evidence/*.evidence.json`はCore Evidence Schemaへ変換したRecordです。Cluster Failureは対象Quorum QueueのLeaderをManagement APIから特定し、その一Nodeだけを停止します。

`delivery.flow-control`はConsumer Prefetchの配送制御を対象にします。RabbitMQ Connection Stateの`flow`とresource alarmによる`blocked`は異なるSurfaceであり、現Epochでは未閉包のため完成Gapとして残します。

