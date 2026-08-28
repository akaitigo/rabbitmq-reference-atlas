# Management Health / Alarm Snapshot

## 採取

障害対応開始時に時刻と対象Clusterを固定し、Management APIから次を同じSnapshotとして保存します。

- `/api/nodes`: Node名、`running`、Memory Alarm、Disk Alarm、Partition
- `/api/queues/{vhost}/{queue}`: 配置Node、Queue Type、State、Ready、Unacknowledged、Consumer数
- 各Nodeの`/api/health/checks/ready-to-serve-clients`: Listener、Alarm、起動状態を含むClient受付可否
- 各Nodeの`/api/health/checks/alarms`: Cluster Alarm

CredentialやHTTP Authorization Headerは保存しません。単一Nodeの応答だけをCluster全体の健全性とは判断せず、三つのManagement endpointを確認します。

## 判定

HTTP 200とJSON `status=ok`を分けずに記録します。Queue backlogでは`messages_ready`と`messages_unacknowledged`を分離し、Consumer障害、処理遅延、Prefetch滞留を切り分けます。Alarm発生時は即時にNodeを再起動せず、Memory/Disk使用量、Publisher Connection Block、Partitionを先に確認します。

## 復旧確認

三Nodeが`running`、Partitionなし、Health/Alarmが`ok`となり、対象Queueの状態とMessage数が説明可能であることを確認します。Application経路では新規Publish ConfirmとConsume/Ackを別途実行し、Management APIの見かけだけで復旧完了にしません。
