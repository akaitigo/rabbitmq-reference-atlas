# 実行Evidenceの原子的保存

`evidence-reporting.yaml`は、実行ReporterがEvidence集合を公開する際のtransaction境界を定義する。参照は`frontend-behavior-atlas` commit `7175de4305afb308722d5b83475e91c18da64957`である。

`make labs`はliveの`evidence/`へ実行途中のArtifactを書かない。次の順序で一つのfull-runを処理する。

現在commit済みのlive Evidenceは本契約導入前の成功集合であり、`bounded-historical-pre-atomic-contract`として保持する。原子的publication済みとは扱わない。次回のfull-run成功時に`evidence/run-report.json`が生成され、そのrunから本契約のpublication記録を持つ。

1. live Evidence全体を同一Filesystem上のsibling stagingへ複製する。
2. `raw/*.json`、`*.evidence.json`、Scenario Proof/Indexをstagingから除き、今回のrunで全件再生成する。
3. baseline所有Pathの欠落、symlink、transaction開始前mtime、新旧混在を検査し、全ArtifactのSHA-256とsizeを`run-report.json`へ記録する。
4. liveをbackupへrenameし、stagingをliveへrenameする。
5. Repository Gate通過後にbackupを破棄する。Gate失敗時は新集合を退避してbackupをliveへ戻す。

Artifact生成、full-run検証、swapのいずれかが失敗した場合、直前成功Evidenceは変更しない。swapの二段目が失敗した場合も、一段目で退避したbackupを即時復元する。個別ファイルのlive上書き、新旧runの混在、失敗runによる直前成功集合の削除は許可しない。

実装は`scripts/evidence_transaction.py`、失敗境界の検証は`scripts/test-evidence-transaction.py`を正本とする。

```sh
make atomic-evidence
make labs
```

`make atomic-evidence`は実RabbitMQを起動せず、隔離した一時Directoryで部分生成、新旧混在、run失敗、swap失敗、正常swapを検証する。`make labs`だけが実Broker/Clientのfull-runを行う。
