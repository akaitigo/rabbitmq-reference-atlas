# Coverage境界

固定対象はRabbitMQ 4.3.5、Canonical ProtocolはAMQP 0-9-1です。このEpochはAMQP Model、4 Exchange Type、Queue、Publisher Confirm、Manual Ack、Redelivery、TTL/DLX、Consumer Prefetch、Resource Alarm/Blocked、Ordering条件、Application Idempotency、Quorum/Stream、三Node Cluster、Leader Failure、Network Partition、Recovery、Least Privilege、Management Observability、固定性能測定を実証します。Upgrade/MigrationとTLSは該当Coverage TargetのEvidence状態を必ず確認します。

以下は存在しない機能ではなく、このRelease Candidateの未閉包領域です。

- AMQP 1.0、Stream Protocol、MQTT、STOMP、WebSocketのProtocol固有挙動
- Stream Protocol、Super Stream、Federation、Shovel
- OAuth 2.0、LDAP、HTTP Authentication Backend
- Kubernetes Operator、VM、Managed Service固有のUpgrade/Backup
- Tanzu RabbitMQ固有機能、Community Plugin、Managed Service固有機能

これらの依頼には`gap`を返し、Authority追加、Coverage Target、Claim、Proof Obligation、Lab、Evidenceが必要だと説明します。固定性能EvidenceをProduction CapacityやQueue Typeの普遍的優劣へ一般化せず、Application LedgerをRabbitMQ組込み重複排除と表現しません。
