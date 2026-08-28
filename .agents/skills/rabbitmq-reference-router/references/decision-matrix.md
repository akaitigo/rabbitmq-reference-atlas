# 判断表

| 問い | まず確認するもの | このEpochでのEvidence |
|---|---|---|
| Routing Keyで配送先を選ぶ | Direct ExchangeとBinding | `messaging.exchange-routing` |
| Broker受理をPublisher側で確認する | Confirm Mode | `delivery.publisher-confirm` |
| Consumer処理完了までMessageを保持する | Manual Ack | `delivery.acknowledgement` |
| 一時失敗後に再配送する | Nack/Requeueと冪等性 | `delivery.redelivery` |
| 恒久失敗を隔離する | DLX、DLQ、x-death | `delivery.dead-lettering` |
| 一Consumerの未Ack数を制限する | Consumer Prefetch | `delivery.flow-control` |
| 一Node停止へ耐える | 三Replica Quorum Queue | `cluster.leader-failure` |
| AMQP Entityの宣言競合を判定する | Property EquivalenceとChannel Error | `messaging.amqp-model-boundary` |
| Routing Patternを選ぶ | Direct/Topic/Fanout/HeadersのBinding規則 | `messaging.exchange-binding-matrix` |
| 時間超過Messageを隔離する | TTL、DLX、`x-death.reason=expired` | `delivery.ttl-dead-lettering` |
| Logを複数Consumerで再読する | Stream QueueとOffset | `queue.stream-semantics` |
| Broker Alarm中のPublishを制御する | Connection.BlockedとAlarm解除 | `delivery.publisher-flow-control` |
| 順序を要求する | 単一Channel/Queue/Consumerの限定条件 | `delivery.ordering-boundary` |
| 重複副作用を抑える | Application Idempotency Ledger | `delivery.idempotency-boundary` |
| Network分断へ耐える | Quorum多数派と少数派拒否 | `cluster.network-partition` |
| 最小権限を設定する | vhost、User、Resource Permission | `security.least-privilege` |
| 性能条件を比較する | 固定WorkloadのRaw Sampleと分位 | `performance.fixed-workload` |

Consumer AckはBrokerからConsumerまで、Publisher ConfirmはPublisherからBrokerまでの異なる境界です。再配送が発生しても業務副作用が一回になる保証は本AtlasのRabbitMQ機能だけでは成立せず、Consumer側のIdempotencyが必要です。
