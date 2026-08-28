# RabbitMQ 技術実証アトラス

`rabbitmq-reference-atlas`は、RabbitMQ 4.3.5の公開挙動を、一次資料、再実行可能なLab、観測可能なOracle、Digestで固定したEvidenceへ接続する製品Technical Reference Atlasです。

現在はDefinitive Coverageを再構築中で、`status: incomplete`です。GitHub Repositoryは公開済みですが、署名済みRelease、OCI Artifact、外部Skill Registryへの公開は行っていません。`22ab07c`時点の32 Target Certificateはbounded historical Evidenceとして保持し、RabbitMQのDefinitive Completionを証明するものとは扱いません。

## 固定対象

- RabbitMQ `4.3.5`
- OCI Image `rabbitmq:4.3.5-management@sha256:45226f38499559b9f56875c752cc6689ff90e8f20796fe80fd9bc28d64723031`
- Coverage Epoch `2026-08-28`
- Core Contract `reference-atlas-core@be19ddaa411fe60dcf12f0f5d457902bb57b9eb3`（Subject Definitive Gate v2）

## Bounded Historical実証範囲

- AMQP 0-9-1 Model、Direct/Topic/Fanout/Headers Exchange、Binding、Queue
- Publisher Confirm、Manual Ack、Nack/Requeue、Redelivery、TTL/DLX
- Quorum Queue、Streamの非破壊Replay、限定条件のOrdering、Application Idempotency境界
- Consumer Prefetchと、Resource AlarmによるPublisher Block/再開
- 三Node Cluster、Quorum Leader停止、Network Partition、多数派配送、Replica復帰
- Least Privilege、Management Health/Alarm、固定Workload性能測定
- Router Skillと全領域・誤同一視を含む決定論的Eval

## Mastery

`mastery.yaml`は、RabbitMQという同一分野で答えられるべき問いを8 Outcomeと14 Surfaceへ固定します。公開済みmainの旧32 Targetは非後退baselineとして`covered`のまま維持し、37 Authority Artifactから抽出した206 Behaviorを追加の専用required Targetへ接続しました。`verification.plan.yaml`には全2,060 Scenario Rowがあり、Surface規則から951 Rowを実Runtimeまたは実Platform必須としているため、Targetや文書の宣言だけで`complete`へ戻りません。

`baseline/public-main-22ab07c.yaml`は、公開済みmainのTarget、Source、Claim、Proof、Evidence、Lab、Go Test、Router Skill Eval、CI MatrixをDigest付きで固定します。`make non-regression`は削除、required/covered格下げ、EvidenceやAssertionの変更、CI縮小を拒否し、承認された置換には`migrations/public-main-baseline-v2.yaml`の旧ID→新ID Mappingと実行Proofを要求します。

`rabbitmq-depth-parity.yaml`は、`frontend-behavior-atlas@4a0b2df8e2091a963bd0e0e1bbccef9c84b49a45`のFE Depth Referenceが定義する18軸を、RabbitMQ固有の母集団へ写像します。FEの件数は閾値にせず、AMQP 0-9-1/1.0、MQTT、STOMP、Stream、Plugin、三Node Cluster、二Cluster、外部認証、TLS、Operator、Upgrade、CapacityのProfileを分離し、各Required Behavior × Scenario × Profileへ専用ProofとArtifactを要求します。FE自身も1/18 satisfiedで`incomplete`であり、本Repositoryも18軸すべてのGapが0になるまでDefinitive昇格を許可しません。

`authority/extraction.snapshot.json`は、`frontend-behavior-atlas@cabf687bab769b17928d950acc416f3f77eb4ca3`のcopyright-safe Locator契約を適用します。第三者本文やexcerptは保存せず、URL、固定digest、取得metadata、Locator byte offset、context/heading digestだけを保持します。現在は50 Source中48件がdigest一致、Docker packagingの2 Sourceがstale、206既存reference edge中137件のLocatorを確認し、69件は未解決です。さらに一致した48 body全体を1,471の非重複Sectionへpartitionし、Section offset/digestだけを保持します。これはSemantic Surface分類ではないため、本文全体のexhaustive抽出を`false`、Human reviewを0件として維持し、Protocol、Plugin、Operatorの各denominatorを`partial`とします。

`authority/body-inventory.snapshot.json`は、`frontend-behavior-atlas@841ec2fa399606a10305021a8bcd396713b8cee5`のAuthority body denominator契約を適用します。50 unique documentの固定bodyに対してRabbitMQ資料形式用の固定selector contractを使い、48一致documentから1,579 raw anchor候補を列挙しました。Artifactにはstable ID、tool/source digest、fragmentまたはbyte offset、context/label digestだけを保存し、第三者本文は保存しません。全1,579件は`pending-human`で、2 stale documentは抽出を保留しています。raw anchor数はSemantic Surface数にもDepth達成にも算入せず、Queueの人手decisionが整合した候補だけをAuthority SurfaceまたはAtomic Behaviorへ昇格できます。初回候補集合は`baseline/authority-body-inventory-v1.json`で固定し、`make authority-body`が無断削除・置換と本文field混入を拒否します。

`authority/review-queue.snapshot.json`は、`frontend-behavior-atlas@de2f016b8b44ea67afdb08c0552044807505984e`のReview Queue契約を適用します。1,579 raw anchorをstable IDのまま171 batchへ一度ずつ投影し、全件を`pending-human`で開始しました。priority、85 candidate cluster、batchは確認順と重複候補の機械提案だけで、Semantic decisionまたはDepth達成には算入しません。2 stale documentはholdです。一次資料を人が確認し、reviewer、時刻、理由と理由digest、source/tool/context digest、locator、旧anchor→新Authority Surface/Atomic Behavior mapping、result集合がすべて整合するdecisionだけを`authority/reviews/decisions.json`で受理します。詳細は`docs/AUTHORITY_REVIEW_WORKFLOW.md`に固定します。

Router Skill Evalは`frontend-behavior-atlas@8a9e34a89a55cc53702032783c06ede7246a286f`の方式をRabbitMQへ適用します。112セルを実Target、Scenario/Profile Variant、Authority locator、固定Broker/Protocol EvidenceへRouteし、曖昧・未知Query、無許可変更、人手Authority判断、stale relockをfail-closedで評価します。現状はRouter契約112/112、Route可能86、実証Closure 20/112、独立Agent Forward Eval 6/10であり、matrix passだけでは完成扱いにしません。詳細は`docs/SKILL_EVAL_WORKFLOW.md`に記録します。

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
- Definitive Contract: `definitive.yaml`
- Authority Surface Inventory: `surface.inventory.yaml`
- Copyright-safe Authority Locator Audit: `authority/extraction.snapshot.json`
- Authority Raw Anchor Inventory: `authority/body-inventory.snapshot.json`
- Authority Human Review Queue: `authority/review-queue.snapshot.json`
- Open Verification Plan: `verification.plan.yaml`
- Depth Parity: `rabbitmq-depth-parity.yaml`
- Skill Package: `skill.package.yaml`
- Skill Eval Detail: `evals/rabbitmq-reference-atlas.skill-routing-eval.json`
- Independent Agent Forward Eval: `evals/rabbitmq-reference-atlas.independent-agent-forward-eval.json`
- Mastery Contract: `mastery.yaml`
- Product Version: `versions/baseline.yaml`
- Claim: `atlas/claims/index.yaml`
- Lab Contract: `labs/*/lab.yaml`
- Evidence: `evidence/*.evidence.json`

RabbitMQはBroadcom Inc.またはその関連会社の商標です。本Repositoryは非公式の独立プロジェクトであり、RabbitMQ TeamまたはBroadcomによる承認・提携を示すものではありません。

なお、使用するContainerはDocker Official Imageですが、RabbitMQ upstream自身の配布物ではなくDocker Community maintained packagingです。RabbitMQ ServerのVersion LockとOCI packagingのDigest Lockを別々に管理します。
