# RabbitMQ 4.2.9から4.3.5へのRolling Upgrade

このLabは、実在する固定OCI Imageを用いてRabbitMQ 4.2.9の三Node Clusterを一台ずつRabbitMQ 4.3.5へ置換し、Mixed-version Phaseを含むCluster Healthと低Rate Workloadの継続を検証します。

## 実行

前提はDocker Engine、Docker Compose、Goです。Labは`rabbitmq-reference-atlas-upgrade`という専用Compose Project、専用Network、専用Volumeだけを操作します。

```bash
bash scripts/run-upgrade-migration-lab.sh
```

成功時は`evidence/raw/upgrade-migration.json`へRaw Evidenceを保存し、Compose Resourceを削除します。調査のため環境を残す場合だけ`KEEP_UPGRADE_ENV=1`を指定します。

## 安全条件

各Nodeを停止する前に次を実行します。

- `rabbitmq-diagnostics check_if_node_is_quorum_critical`
- `rabbitmq-diagnostics check_if_new_quorum_queue_replicas_have_finished_initial_sync`
- `rabbitmq-upgrade await_online_quorum_plus_one`
- `rabbitmq-upgrade drain`

Node Name、Data Volume、Erlang Cookie、Cluster設定を維持し、`upgrade-3`、`upgrade-2`、`upgrade-1`の順に置換します。全Node置換後に全Stable Feature Flagを有効化し、Queue LeaderをRebalanceします。

## Evidenceの読み方

`snapshots`は`source`、`mixed-1`、`mixed-2`、`target`の順に、Node Version、Membership、Resource Alarm、Network Partition、Queue State、Quorum Replicaを記録します。`workload.phase_stats`は各安定PhaseでPublisher ConfirmとConsumer Ackが成立した件数を示します。

`missing_confirmed_ids`が空であることは、このLabでConfirmを受け取ったMessage IDが最終的にConsumerへ到達したことを意味します。Confirm応答喪失時の再試行によりDuplicateが発生し得るため、`duplicate_deliveries`は観測値として保持します。

## 証明しないこと

- RabbitMQはDowngradeを公式サポートしないため、4.3.5のData Directoryを4.2.9で再利用しません。
- このLabは別ClusterへのDefinitions移行、Federation、Shovel、Message移送を実行しません。
- 低Rate Workloadは可用性Probeであり、Upgrade中のThroughput、Latency、容量を保証しません。
- Publisher ConfirmとConsumer AckはExactly-once Deliveryを意味しません。
- RabbitMQ Release Informationは4.2.10を掲載していますが、調査時点では`v4.2.10` Tagと対応するDocker Official Imageが存在しません。このため、実在してDigest固定できる4.2.9をSourceとします。
