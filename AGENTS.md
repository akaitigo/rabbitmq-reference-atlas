# Repository instructions

このリポジトリは、RabbitMQ 4.3.5を固定対象とする製品Technical Reference Atlasです。

- 利用者向け文書、Skill、CLIメッセージは日本語を正本にする。
- Schema Key、ID、Path、API名、RabbitMQの正式名称は英語表記を維持する。
- `atlas.yaml`、`sources.lock.yaml`、`coverage.yaml`、`skill.package.yaml`は共通契約に従う。
- RabbitMQ固有情報はCore Schemaへ追加せず、`versions/`、`surface/`、`atlas/`、`labs/`に置く。
- EvidenceなしにCoverageを`covered`へ変更しない。
- 全Completion Gate通過前は`atlas.yaml`の`status: incomplete`を維持する。
- Failure LabはこのRepositoryのDocker Compose projectだけを対象とし、外部環境を操作しない。
- GitHub公開、Release、署名、外部Registry登録は明示的な許可なしに行わない。

