# 性能・容量Evidenceの読み方

`make labs` は固定したローカル条件でClassic、Quorum、Stream Queueへ各300件を送受信し、Publisher Confirm latencyのRaw Sample、p50/p95/p99、Publish/Consume rate、Broker memory snapshotを記録する。

この測定はHarnessと環境が正常に性能値を採取でき、同じ固定条件を再実行できることを証明する。共有Host上の一回のSynthetic Workloadなので、最大Throughput、Production SLO、Hardware非依存の容量、Queue Typeの普遍的な優劣は証明しない。本番判断ではPayload、Publisher/Consumer数、Backlog、Replica、Disk、Network、Failure時余力を本番相当条件へ変更し、複数反復して分布を比較する。
