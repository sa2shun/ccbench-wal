# ermia_pwal Step B 実装メモ (P-WALロギング組み込み)

## 方針
cc/ermia_pwal 内だけを変更する(共有ヘッダ ../../include/*, cc/ermia は触らない)。
プロトタイプ pwal/pwal.h の構造を ermia_pwal 用に移植する。

## 新規ファイル: include/pwal.hh
- LogRecord: {lsn, type(Update/End), cstamp, key} 固定32バイト。
  値ペイロードは含めない(比較実験では全手法で同一条件なので公平性は保たれる。メモ化)。
- PwalLsnCounter: 共有カウンタ(fetch_add, relaxed)。既存のCstamp用 `Lsn` とは別の2本目。
- PwalWorker: スレッド専用の {WALバッファ(vector), WALファイルfd, flushedLSN(atomic, alignas64),
  コミット待ちqueue(deque), notified件数}。
  - append(type, cstamp, key) → lsn
  - flush(): バッファ全書き + fdatasync + flushedLSN更新 + バッファclear (プロトタイプと同じ契約)
  - controlNotification(全ワーカ): commitLSN <= min(flushedLSN) を確定、notified++
  - drainPending(): 終了時用(バッファ空を検査)
- グローバル: PwalWorkers (std::vector<PwalWorker*>)。mainでresize、各workerが自スロット登録。

## 変更ファイル
1. include/common.hh: `GLOBAL std::vector<PwalWorker*> PwalWorkers;` と
   gflags DECLARE (pwal_flush_ntx, pwal_dir)。
2. ycsb_ermia_pwal.cc:
   - DEFINE_uint64(pwal_flush_ntx, 10), DEFINE_string(pwal_dir, "pwal_logs")
   - main: ログディレクトリ作成、PwalWorkers.resize(TotalThreadNum)
   - worker(): PwalWorker をスタックに作り自スロットへ登録(ready通知の前)。
     quit後: 最終flush。
   - main: join後に各ワーカのdrainPending、notified合計を
     `pwal_notified_commits: X` として標準出力に追加(既存の結果表示は変更しない)。
3. transaction.cc: ssn_parallel_commit() の `// logging` プレースホルダ(1箇所, :789相当)で
   - write_set_ の各要素につき Update レコード(cstamp, key先頭8バイト)を append
   - End レコードを append → commitLSN
   - コミット待ちqueueに {cstamp, commitLSN} を push
   - read-onlyトランザクション(write_set_空)はログを書かず即 notified 扱い
   ssn_commit()(YCSB経路では未使用)は変更しない。
4. transaction.cc or transaction.hh: TxExecutor::commit() の成功パス末尾で
   - コミット済みtx数をカウントし、pwal_flush_ntx 件ごとに flush + controlNotification。

## 測定の扱い(Step Bでは最小限)
- 既存の commit_counts_/throughput 表示はそのまま(=実行完了したtx数)。
- 永続化済み+通知済みの件数は `pwal_notified_commits` として別行で出す。
  定常状態では両者はほぼ一致するはず(差はflush間隔分)→動作確認に使う。
- レイテンシ計測は測定ステップで追加する(今回はやらない)。

## 監査後の修正・決定事項
- PwalWorkerの生成はworker()スタックではなくmainがスレッド起動前にnewする
  (join後のdrainで生存している必要があるため。可視性もmainのthread生成前で安全)。
- 親ccbench/CMakeLists.txtのプロトコル一覧への1行追加は登録に必要な例外。
- アイドルワーカ対策: バッファ空のflush時は、採番済み最大LSNまで自分のflushedLSNを
  進める(未flushログを持たないワーカはminを制約しないため安全)。これをしないと
  writeログを書かないワーカがmin(flushedLSN)を固定し全体の通知が止まる。
- 既知の単純化(全手法共通なので比較は公平):
  - read-only txは永続化を待たず即confirm(厳密には読んだ書き込みのflushを待つべき)。
  - ログレコードに値ペイロードなし、DELETE/INSERTもUpdate型で記録。
  - pwal層の例外(open/write/fdatasync失敗)はcatchせずterminate。

## 動作確認
- ビルド + YCSB-A相当(-ycsb_rratio=50)小規模実行。
- pwal_notified_commits が commit_counts_ 合計と近いこと(差 <= スレッド数×flush_ntx 程度)。
- WALファイルが生成されサイズ>0であること。
- ベースライン ermia(無変更)が引き続きビルドできること。
