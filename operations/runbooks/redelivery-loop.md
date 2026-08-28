# Redelivery Loop

同一Message IDと`redelivered`、Queueのdeliver/get rate、Consumer logを関連付けます。恒久エラーを無条件に`Nack(requeue=true)`し続けません。原因を分類し、上限付きRetryまたはDead Letteringへ移します。AckはDeliveryを受信したChannelからだけ行います。

