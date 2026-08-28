# Quorum Queue Leader停止

## 事実確認

`rabbitmq-diagnostics cluster_status`とManagement APIで、Running Members、対象QueueのLeader、Online Membersを確認します。三Replica中二Replicaが稼働していない場合は、本Labの安全な回復条件を満たしません。

## 安全な一次対応

多数派を維持し、同時に複数Nodeを再起動しません。Clientは複数Endpointを使って再接続し、Channel、Confirm Mode、QoS、Topology、Consumerの順に復元します。

## 復旧判定

- 対象Queueが利用可能である。
- Confirm済みMessageを説明可能な重複範囲で回収できる。
- 停止NodeがRunning Membersへ復帰する。
- 新規PublishのConfirmとConsume/Ackが成功する。

