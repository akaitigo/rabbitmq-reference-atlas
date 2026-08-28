# Dead Letter Queue Backlog

DLQを通常Queueへ無条件に一括Replayしません。`x-death`、Message ID、失敗原因、現在のConsumer修正状態を確認し、重複副作用を防ぐIdempotency境界を用意してから小さなBatchで再投入します。DLXと隔離Queueが存在し、Publish可能であることも先に確認します。
