# Authority raw anchor review workflow

`authority/review-queue.snapshot.json`は、固定済みRabbitMQ一次資料から列挙したraw anchorを、人が一次資料を確認できる作業Queueへ完全投影した記録である。Queue投入、優先度、cluster、batchはSurface分類の結論ではない。各anchorは有効な人手decisionが記録されるまで`pending-human`である。

## 入力と機械提案の境界

- `anchor_id`は`baseline/authority-body-inventory-v1.json`で固定したIDを変更せず使用する。
- 各Queue itemはdocument URL、locked source digest、body inventory tool digest、review queue tool digest、locator、固定本文内のcontext範囲とdigestを持つ。第三者本文、見出し、引用は保存しない。
- `proposed_priority: 0`は既存Domain reference locatorとの一致、`1`はlabelを持つ見出しまたは定義、`2`は構造またはdocument anchorを示す。これは確認順の提案である。
- `proposed_candidate_cluster_id`は同じselector kindとlabel digestを持つ重複候補の提案であり、意味の同一性やmergeを決定しない。
- `proposed_batch_id`はpriority、selector kind、anchor ID hash bucketで決定論的に作る作業単位であり、Semantic decisionではない。
- 2件のstale documentは`stale_holds`へ置く。Source lockを更新し、本文digestを再固定してbody inventoryを再生成するまでQueueへ投入しない。
- Queue item数、priority数、cluster数、batch数はSemantic Surface数またはDepth達成へ算入しない。

## 人手Review

ReviewerはQueue itemの`document_url`と`locator`を一次資料で開き、必要ならlocked digestと一致する取得bodyのcontext offsetを確認する。自動処理、Agent、cluster提案だけをreviewerまたは決定根拠にできない。

`authority/reviews/decisions.json`の各decisionには次を必要とする。

- 一意な`decision_id`、`include`／`exclude`／`merge`／`split`、対象`anchor_ids`
- Queue itemと完全一致する`source_bindings`。document ID/URL、locked source digest、inventory/queue tool digest、locator、context/label digestを含む
- 40文字以上の具体的な`reason`と、そのUTF-8 SHA-256である`reason_digest`
- 人の`reviewer`、timezoneを持つISO date-timeの`reviewed_at`、`review_method: manual-primary-source`
- 全旧anchorを覆う`mapping`。includeは1件以上の新ID、excludeは空、mergeは複数旧IDから同じ新ID集合、splitは1旧IDから2件以上の新IDへ対応する
- mapping先と同じID集合を持つ`result_items`。各IDを`authority-surface`または`atomic-behavior`として明示する

同じanchorへ複数decisionを作れない。merge以外で新IDを共有できない。Queue導入時の206 Authority Surfaceと206 Atomic Behaviorは`baseline/authority-review-prequeue-v1.json`に固定する。それ以後に追加するSurfaceまたはBehaviorは、有効なdecision resultに同じIDとtypeが存在しない限りVerifierが拒否する。

## 実行

```bash
python3 scripts/generate-authority-review-queue.py
python3 scripts/test-authority-review-queue.py
python3 scripts/validate-authority-review-queue.py
```

Source lock、body inventory、抽出tool、Queue tool、locatorが変わった場合、古いbindingを自動採用せず検証を失敗させる。未処理anchorまたはstale holdがある間は`status: incomplete-human-review-required`と`authority_semantics_exhaustive: false`を維持する。
