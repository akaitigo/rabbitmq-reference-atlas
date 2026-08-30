# Scenario gap Closure

`scenario-closure.yaml`は、各RabbitMQ SurfaceのScenario gapを閉じるための実行契約である。参照したFE契約は`frontend-behavior-atlas` commit `f2e4c4b19156f8e993f48cdcbce23679ad881924`であり、件数ではなく専用実行と証拠の粒度をRabbitMQへ写像する。

Closure単位はAuthority Surface × Scenario × 全Runtime/Semantic Variantである。各Required rowについて、指定Profileの全Variantを専用の実RabbitMQ Brokerと実Clientで駆動し、次を同じreportへ記録する。

- reportと各Variantの`attempts: 1`、`retries: 0`
- Broker product/version/image digest、Client name/version/source digest、Runtime profile/platform/execution ID
- Variant固有Oracle ID、assertion集合、pass結果
- SourceとHarnessのrepository path、SHA-256、および実在ファイルとの一致
- Variant専用のpacket、log、metric Artifact path、SHA-256、size

reportは`evidence/scenario-runtime/<behavior>/<scenario>.runtime.json`、Artifactは`evidence/scenario-runtime/artifacts/<behavior>/<scenario>/<variant>/<channel>.*`へ置く。全channelと全rowでArtifact pathを共有できない。`reference-system/`の統合結果、`evidence/raw/`の既存Artifact、別rowまたは別channelのArtifact metadataは流用できない。

`decision-comparison` SurfaceにはProfile Variantに加えて2個以上のSemantic Variant inventoryが必要である。未宣言の比較は`variant.semantic-inventory-missing`として開いたままにする。

既存の12 Runtime Evidenceは削除せず`legacy_observation`として記録するが、`counts_toward_scenario_gap_closure`は常にfalseである。Scenario gap Closure後も、Authority Review Queueで人手確認されたAtomic behavior bindingがなければCompletion eligibleにはならない。

検証は次で実行する。

```sh
python3 scripts/generate-scenario-proofs.py
python3 scripts/test-scenario-proofs.py
python3 scripts/validate-scenario-proofs.py
```

現時点はRequired 951 row、legacy runtime observation 12、専用runtime report 14、Scenario gap closed 14、Completion eligible 0である。専用reportはAMQP 1.0のnormal/boundary/rejection/security、MQTTとSTOMPのcompatibility、Quorumのfailure、Partitionのoperations/recovery、Management認可のsecurity、node healthのoperations、plugin inventoryのcompatibility、client接続のfailure/recoveryをRabbitMQ 4.3.5の3-node runtimeで駆動し、3 Variant × 3 channelの126 Artifact bindingを保持する。残り937 rowとAuthority atomic bindingは未Closureである。
