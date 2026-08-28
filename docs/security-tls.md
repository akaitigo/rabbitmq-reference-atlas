# TLS/mTLS Transport Lab

このLabはRabbitMQ 4.3.5のTransport境界だけを、既存Clusterから分離した単一Node環境で検証する。

## 証明すること

- 固定OCI ImageがPlaintext AMQP listenerを持たず、TLS listenerだけを公開する。
- ClientがEphemeral CAを明示的に信頼し、`rabbitmq-tls`をServer Nameとして検証し、Client証明書を提示した場合だけAMQPS Messagingが成功する。
- 不正CA、Hostname不一致、Client証明書なし、Plaintext AMQPは成功として扱われない。
- 実際に提示されたServer証明書のSHA-256 Fingerprintが、生成したServer証明書と一致する。

## 実行

```bash
bash scripts/run-tls-lab.sh
```

結果は`evidence/raw/security-tls.json`へ保存される。Raw Evidenceには証明書Fingerprintと判定だけを残し、秘密鍵、Password、証明書の一時Path、TLS Error本文は保存しない。

## 秘密情報の扱い

CA、Server、Clientの秘密鍵は`mktemp -d`で作成したRuntime一時領域にだけ存在する。終了時は成功・失敗にかかわらずTrapが専用Compose projectを停止し、一時領域を削除する。Git管理対象へ秘密鍵を生成しない。

このLabのCAは実証専用であり、Production用PKI、Certificate Lifecycle、失効、Rotationを保証しない。
