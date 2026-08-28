# Coverage境界

固定対象はRabbitMQ 4.3.5、Canonical ProtocolはAMQP 0-9-1です。このEpochで実証するのはExchange、Queue、Publisher Confirm、Manual Ack、Redelivery、Dead Lettering、Consumer Prefetch、三Node Cluster、Quorum Queue Leader Failure、Node Recoveryです。

以下は存在しない機能ではなく、このRelease Candidateの未閉包領域です。

- AMQP 1.0、Stream Protocol、MQTT、STOMP、WebSocketのProtocol固有挙動
- Stream、Super Stream、Federation、Shovel
- TLS、mTLS、OAuth 2.0、LDAP、HTTP Authentication Backend
- Connection State `flow`、memory/disk alarmによる`blocked`
- Kubernetes Operator、VM、Upgrade、Backup/Restore、性能容量モデル
- Tanzu RabbitMQ固有機能、Community Plugin、Managed Service固有機能

これらの依頼には`gap`を返し、Authority追加、Coverage Target、Claim、Proof Obligation、Lab、Evidenceが必要だと説明します。

