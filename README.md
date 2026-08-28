# RabbitMQ 技術実証アトラス

`rabbitmq-reference-atlas`は、RabbitMQ 4.3.5の公開挙動を、一次資料、再実行可能なLab、観測可能なOracle、Digestで固定したEvidenceへ接続する製品Technical Reference Atlasです。

現在はローカル実装段階で、`status: incomplete`です。GitHubには未公開です。完成の主張は、固定Coverage Epochに対する全Gateと生成Certificateが揃うまで行いません。

## 固定対象

- RabbitMQ `4.3.5`
- OCI Image `rabbitmq:4.3.5-management@sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031`
- Coverage Epoch `2026-08-28`
- Core Contract `reference-atlas-core@1c85bed8d45a3daee3e5cda7fbbe144607ac1259`

## 実証範囲

- Direct Exchange、Queue、Binding、Publisher Confirm
- Manual Acknowledgement、Nack/Requeue、Redelivery Flag
- Dead Letter Exchangeと隔離Queue
- Consumer PrefetchによるFlow Control
- 三ノードCluster、Quorum Queue、Leader停止、配送継続、Node復帰
- Router Skillと決定論的Eval

## 検証

前提はGo 1.26、Python 3、Docker Engine、Docker Composeです。

```bash
make check
make labs
```

`make labs`は専用Compose projectを作成し、正常系、拒否・再配送、Cluster障害、復旧を実行して`evidence/`を再生成した後、Volumeを含めてCleanupします。調査のため環境を残す場合だけ`KEEP_ENV=1 make labs`を使用します。

## 正本

- Atlas Identity: `atlas.yaml`
- Authority Lock: `sources.lock.yaml`
- Coverage: `coverage.yaml`
- Skill Package: `skill.package.yaml`
- Product Version: `versions/baseline.yaml`
- Claim: `atlas/claims/index.yaml`
- Lab Contract: `labs/*/lab.yaml`
- Evidence: `evidence/*.evidence.json`

RabbitMQはBroadcom Inc.またはその関連会社の商標です。本Repositoryは非公式の独立プロジェクトであり、RabbitMQ TeamまたはBroadcomによる承認・提携を示すものではありません。

なお、使用するContainerはDocker Official Imageですが、RabbitMQ upstream自身の配布物ではなくDocker Community maintained packagingです。RabbitMQ ServerのVersion LockとOCI packagingのDigest Lockを別々に管理します。
