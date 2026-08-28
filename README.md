# RabbitMQ 技術実証アトラス

`rabbitmq-reference-atlas`は、RabbitMQ 4.3.5の公開挙動を、一次資料、再実行可能なLab、観測可能なOracle、Digestで固定したEvidenceへ接続する製品Technical Reference Atlasです。

現在は固定Coverage Epochに対するローカル実証を完了し、`status: complete`です。GitHub Repositoryは公開済みですが、署名済みRelease、OCI Artifact、外部Skill Registryへの公開は行っていません。完了の範囲は`atlas.yaml`、`mastery.yaml`、`coverage.yaml`とCompletion Certificateに固定しています。

## 固定対象

- RabbitMQ `4.3.5`
- OCI Image `rabbitmq:4.3.5-management@sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031`
- Coverage Epoch `2026-08-28`
- Core Contract `reference-atlas-core@d5c0a6ce757fd5f43af837edd26f55c7325b811e`

## 実証範囲

- AMQP 0-9-1 Model、Direct/Topic/Fanout/Headers Exchange、Binding、Queue
- Publisher Confirm、Manual Ack、Nack/Requeue、Redelivery、TTL/DLX
- Quorum Queue、Streamの非破壊Replay、限定条件のOrdering、Application Idempotency境界
- Consumer Prefetchと、Resource AlarmによるPublisher Block/再開
- 三Node Cluster、Quorum Leader停止、Network Partition、多数派配送、Replica復帰
- Least Privilege、Management Health/Alarm、固定Workload性能測定
- Router Skillと全領域・誤同一視を含む決定論的Eval

## Mastery

`mastery.yaml`は、RabbitMQという同一分野で答えられるべき問いを8 Outcomeと14 Surfaceへ固定します。運用監視、Security、性能、互換性、移行を含む32 Required TargetはClaimと再実行可能なEvidenceへ接続済みです。Repository数や対象分野を増やすためのManifestではありません。

## 検証

前提はGo 1.26、Python 3、Docker Engine、Docker Composeです。

```bash
make check
make labs
```

`make labs`は専用Compose projectを作成し、正常系、拒否・再配送、Resource Alarm、Cluster障害、Network Partition、復旧、Security、Observability、固定性能測定を実行して`evidence/`を再生成した後、Volumeを含めてCleanupします。調査のため環境を残す場合だけ`KEEP_ENV=1 make labs`を使用します。

## 正本

- Atlas Identity: `atlas.yaml`
- Authority Lock: `sources.lock.yaml`
- Coverage: `coverage.yaml`
- Skill Package: `skill.package.yaml`
- Mastery Contract: `mastery.yaml`
- Product Version: `versions/baseline.yaml`
- Claim: `atlas/claims/index.yaml`
- Lab Contract: `labs/*/lab.yaml`
- Evidence: `evidence/*.evidence.json`

RabbitMQはBroadcom Inc.またはその関連会社の商標です。本Repositoryは非公式の独立プロジェクトであり、RabbitMQ TeamまたはBroadcomによる承認・提携を示すものではありません。

なお、使用するContainerはDocker Official Imageですが、RabbitMQ upstream自身の配布物ではなくDocker Community maintained packagingです。RabbitMQ ServerのVersion LockとOCI packagingのDigest Lockを別々に管理します。
