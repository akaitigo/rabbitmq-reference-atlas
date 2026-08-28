# RabbitMQ Skill Eval Workflow

RabbitMQ Reference Routerは、8 Outcome × 14 Surfaceの112セルを、`coverage.yaml`のTarget、`verification.plan.yaml`のScenario/Profile Variant、`surface.inventory.yaml`のAuthority locator、固定RabbitMQ RuntimeのBroker/Protocol Evidenceへ接続する。

`make skill-eval`は次の三層を別々に記録する。

1. 公開済み21 Router Caseを再実行し、非後退を確認する。
2. 112セルと5 Boundary Caseを決定論Routerで評価する。曖昧・未知Query、無許可変更、人手Authority判断、stale Source relockはfail-closedとする。
3. 期待値を隠したPromptを別Agentへ与え、実利用QueryのForward Evalを採点する。決定論Routerの自己出力をForward Evalとして扱わない。

詳細結果は`evals/rabbitmq-reference-atlas.skill-routing-eval.json`、独立Agentのraw応答と採点結果は`evals/forward-agent-response.json`および`evals/rabbitmq-reference-atlas.independent-agent-forward-eval.json`を正本とする。Core v2用の`evals/rabbitmq-reference-atlas.definitive-skill-eval.json`は既存8 Caseを保持し、112セルとBoundary結果をSchema準拠形式で投影する。

Router契約matrixのpassはRepository完成を意味しない。各セルのClosureには、Target=`covered`、固定Variant=`covered`、一次資料source/locator/digest、実RabbitMQ Broker identity、Protocol、pass Evidenceが必要である。さらに全Required Target=`covered`、routing gap 0、独立Agent Forward Eval全件passを満たすまで`status: incomplete`を維持する。

現状の機械記録は、Router契約112/112、Route可能86、Mastery routing gap 26、実証Closure 20/112、Target stateは`covered` 35・`planned` 203である。独立Agent Forward Evalは10件中6件passで、Queue Type比較、STOMP検証、Metrics運用、Partition復旧の4件がTarget/Evidenceへ到達していない。この失敗はGapとして保持し、matrix passや安全な停止だけでCompletionへ算入しない。
