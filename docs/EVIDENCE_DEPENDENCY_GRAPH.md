# Evidence Dependency Graph

`evidence/dependency-graph.json`は、Core正式main commit `072d7ca77981f51754e824d70c6d4ecd55ea67e5`の契約をRabbitMQ Evidenceへ適用する。Frontendの件数は閾値として使用しない。

## 入力母集団

14 inputは、一次資料・Coverage、producer client、consumer client、Evidence Reporter、Skill Eval Harness、RabbitMQ Broker／Client runtime、Broker config／3-node Topology、delivery／ack／retry／DLX、quorum／failure／recovery、performance／compatibility、security／observability、Scenario／Reference System profile、Skill／Mastery profile、既存Runtime observation集合を分離する。各inputの`members`と集合Digestを固定し、対象、試験、Profile、閾値をScope外へ退避しない。

## 出力と再実行

Graph生成器は`evidence/raw/*.json`、`evidence/*.evidence.json`、Skill Eval、Reference System gap snapshot、Scenario index、Closure Plan、全Behavior Scenario Proof、`evidence/scenario-runtime/`の専用reportとpacket／log／metric ArtifactをRepository実体から列挙する。実Broker／Client outputは、Command、RabbitMQ 4.3.5、Profile、Runtime identity、`attempts: 1`、全祖先inputの現在Digestをrunへ記録する。入力変更時は影響runの開始が`observed_at`以後でなければ`stale`となり、Graph Digestやbindingだけの更新では閉じない。現行Graphは14 input、2,227 required output、46 runを列挙し、9専用reportと81 Artifact bindingを含む。

`evidence/scenarios/closure-plan.json`は951 required rowをrisk順、1 tranche最大4 rowで固定する。Scenario Proof indexの2,060 rowと併せ、行削除、順序退避、Variant差替え、tranche肥大化を構造変更として拒否する。現在は9 rowがcompleted、942 rowがplannedである。

## 検証

```bash
make evidence-dependency
make atlas-validate
```

`fixtures/evidence-dependency/`は、入力変更後のDigestだけの再固定、run output漏れ、Graph外退避、Closure構造縮小を独立に注入する。`baseline/evidence-dependency-graph-v1.json`は導入時のinput member、Profile、output topology、required output、Proof／Plan構造を加法baselineとして固定する。

`make labs`ではGraphとClosure PlanもEvidence stagingへ生成し、full-run pass時だけ他のEvidenceと同じdirectory renameで公開する。失敗runは直前成功GraphとEvidenceを保持する。Definitive CertificateはGraphが`current`であり、Certificate payloadがGraph Digestへ結ばれる場合だけ生成できる。
