# セキュリティ方針

本RepositoryのFailure Labは、防御、検証、教育だけを目的とします。`com.rabbitmq.reference-atlas`という専用Docker Compose projectと、その内部Network、Volumeだけを操作します。第三者環境、共有Cluster、本番環境を対象にしません。

脆弱性または秘密情報の混入を発見した場合は、公開Issueへ認証情報や再現用Secretを書かず、Repository Ownerへ非公開で連絡してください。実在Credential、個人情報、社内URLをEvidenceへ保存しないでください。

