# Definitive Completion Status

本RepositoryのDefinitive Completionは未完であり、`atlas.yaml`は`status: incomplete`とする。

`22ab07c`時点のCompletion Certificateは、32 Targetと21 Evidence Recordの範囲内では有効な実証履歴である。ただし、RabbitMQ Serverと公式PluginのAuthority Surface全体をInventory化せず、主要GapをCoverage Targetの外に置いたため、Definitive Certificateではない。履歴のCommit、Digest、制約は`evidence/historical/index.yaml`に固定する。

## Gapの正本

Gapの一覧とClosure状態は`coverage.yaml`を正本とする。本文書にだけ機能名を並べてCoverageの対象外にする運用は行わない。

Authority Inventoryで発見したSurfaceは、実装の有無にかかわらずRequired Targetとして`coverage.yaml`へ登録する。Required TargetはClaim、Proof Obligation、実Runtime Lab、Artifact Evidence、Skill Evalが揃うまで`planned`または`partial`とし、`complete`へ戻さない。従来除外されていたSurfaceをExcluded、Infeasible、Scope外へ退避してClosureを成立させることはしない。

## 現在の再監査結果

- 旧32 Target: `covered`。公開済みmainの非後退baselineとして、旧Authority Lockに束縛された21 Evidence Record、17 Lab、32 Claim、7 Proof File、4 Go Test File、21 Router Eval Case、CI Matrixを維持する。
- Authority Artifact: 37。RabbitMQ公式の固定commitと固定Versionから抽出した。
- Authority Behavior: 206。集約Targetを禁止し、Behaviorごとに専用required Targetとproposed Claimを持つ。
- Verification Plan: 2,060 Row。全Behaviorに10 Scenarioを分類し、951 Rowが実Runtimeまたは実Platform必須である。
- Definitive Skill Eval: 8 Outcomeと14 Surfaceを含むが、Closure前なので全Caseを`inconclusive`とする。

未Closureの正本は`coverage.yaml`、Behavior分類は`surface.inventory.yaml`、Scenario単位の作業状態は`verification.plan.yaml`である。Core v2形式の`verification.matrix.yaml`は専用Proof、pass Evidence、Artifactが実在するRowだけで構築し、空欄を成功扱いするためには作らない。

Baseline正本は`baseline/public-main-22ab07c.yaml`、非後退Gateは`scripts/validate-non-regression.py`である。置換を行う場合は`migrations/public-main-baseline-v2.yaml`へ旧ID→新ID、同等以上の実行Proof、Migration Evidence、理由を登録しなければならない。

実行性とEvidence密度の監査は`rabbitmq-depth-parity.yaml`を正本とする。指定されたFE Depth Referenceの18軸を同じ意味とProof粒度で保持し、RabbitMQ側では206 Atomic Behavior、951 Required Scenario Row、15のProtocol/Plugin/Cluster/Platform Profileを母集団とする。Surface数やFEとの件数比を合格根拠にせず、各軸のGapが0になるまでDefinitive昇格を許可しない。参照commitと7 ArtifactのDigestは`parity/frontend-depth-reference.yaml`に固定する。
