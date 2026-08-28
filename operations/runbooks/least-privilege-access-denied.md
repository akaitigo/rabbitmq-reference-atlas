# Least-Privilege Access Denied

## 判定

ApplicationのAMQP操作が`ACCESS_REFUSED`（reply code 403）になった場合、まず接続先vhost、user、対象resource、操作種別を確認します。Password、CredentialをLogやTicketへ記録しません。RabbitMQの`configure`、`write`、`read`は別の権限であり、管理者TagだけでAMQP resource権限が付与されるとは判断しません。

## 安全な確認

Management APIの`/api/permissions/{vhost}/{user}`で現在の正規表現を取得し、必要なExchange／Queue名と照合します。許可確認にはApplicationと同じuserを使った最小のDeclare、Publish Confirm、Consume/Ackを用います。拒否確認は専用vhost内の使い捨てresourceで行い、Production resourceへprobeを送信しません。

## 修復

`.*`への一括緩和は行いません。必要なresource prefixと操作だけを許可し、変更前後のpermission、許可操作、拒否操作をEvidenceへ残します。権限変更後は既存Connectionの再接続を含めて確認します。

## 完了条件

- 必要な操作がPublisher ConfirmとConsume/Ackまで成功する。
- 許可範囲外のconfigure/readが403で拒否される。
- 検証用user、vhost、resourceが削除され、GET 404で不存在を確認できる。
