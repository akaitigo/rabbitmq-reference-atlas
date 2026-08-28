# Queue StateとPrometheus Metricsの相関

## Snapshotの順序

Queue backlogを調べるときは、Management APIの`messages_ready`、`messages_unacknowledged`、`consumers`を同一時刻帯で保存します。Publish直後の`ready`、Consumer配送後の`unacked`、Ack後の`acked`は別の状態であり、一つの総数だけからConsumerの健全性を判断しません。Management統計にはSampling遅延があるため、単発GETではなく時刻付きの上限付きPollを行います。

## Prometheusとの相関

各NodeのPrometheus endpointは`15692`です。本環境ではHostへPort Publishせず、対象Container内の`127.0.0.1:15692/metrics`から取得します。三Nodeすべての取得結果についてPayload byte数、SHA-256、Metric Family数を残し、少なくとも次を確認します。

- `rabbitmq_alarms_free_disk_space_watermark`
- `rabbitmq_alarms_memory_used_watermark`
- `rabbitmq_unreachable_cluster_peers_count`

HTTP取得成功だけではQueue状態を証明できません。Management APIのQueue Snapshot、Application経路のPublisher Confirm／Manual Ack、PrometheusのNode Snapshotを同じRun IDのEvidenceとして関連付けます。

## 安全な対応

`ready`増加はConsumer不足または処理能力不足、`unacked`増加は処理遅延、Prefetch過多、停止したConsumerを候補にします。Memory/Disk Alarmや到達不能Peerがある場合、無条件のQueue purge、Consumer一斉再起動、複数Node同時再起動は行いません。原因とMessageの再配送・重複範囲を確認してから、変更を一つずつ適用します。

## 復旧完了

- 新規PublishがConfirmされる。
- `ready → unacked → acked`を時刻付きで説明できる。
- 三NodeのMetricsを取得でき、Alarmと到達不能Peerの値を説明できる。
- 検証Queueが削除され、Management APIで404になる。
