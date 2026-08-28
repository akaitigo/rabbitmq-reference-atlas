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

Consumer AckはBrokerからConsumerまで、Publisher ConfirmはPublisherからBrokerまでの異なる境界です。再配送が発生しても業務副作用が一回になる保証は本AtlasのRabbitMQ機能だけでは成立せず、Consumer側のIdempotencyが必要です。

