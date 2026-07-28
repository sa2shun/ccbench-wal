# ERMIA統合系へのレイテンシ計測追加メモ

## 定義
クライアント視点の応答時間 = トランザクション開始(最初の試行のbegin)から
コミット通知(P-WALのコミット条件成立、read-onlyは即時)まで。
アボート→リトライは同一トランザクションの継続なので、開始時刻は最初の試行のまま保持する。

## 対象
ermia_pwal と ermia_ayame に同じ変更を入れる。ermia(ベースライン1)は無変更のまま
(ロギングなしでは commit=通知 なので、ccbench既存の導出レイテンシで近似できる。
per-txパーセンタイルはWAL系2系統の比較に使う)。

## 変更内容 (両系統共通)
1. include/pwal.hh:
   - CommitEntry に start (steady_clock::time_point) を追加。
   - Worker::pushCommit(cstamp, lsn, start) / notifyDirectly(start) に変更。
     notifyDirectly も now-start をレイテンシとして記録する。
   - controlNotification / drainPending でpop時に now-start (ns) を
     ワーカローカルの latencies_ns_ (vector) に記録。nowはループ外で1回取得。
   - latenciesNs() アクセサ。コンストラクタで reserve(1<<20) しておき
     測定中の再確保ノイズを減らす。
2. include/transaction.hh: TxExecutor に
   - bool tx_active_ = false;
   - std::chrono::steady_clock::time_point tx_start_;
3. transaction.cc:
   - begin(): if (!tx_active_) { tx_active_ = true; tx_start_ = now; }
     (リトライ時のbegin()では開始時刻を保持)
   - loggingセクション: pushCommit(cstamp, commit_lsn, tx_start_) /
     notifyDirectly(tx_start_)。直後に tx_active_ = false。
4. ドライバ:
   - join後、drain前に各ワーカの latenciesNs().size() を記録(drain分は
     全ワーカ終了待ちを含むため分位点集計から除外)。
   - 全ワーカのレイテンシ(drain分除く)を結合してソートし、
     pwal_lat_avg/p50/p95/p99 [ns] を出力(nearest-rank法)。

## 動作確認
- YCSB-A/B/C 小規模実行で commit_counts == pwal_notified_commits 維持、
  レイテンシ出力が妥当なオーダー(µs〜ms)であること。
- 1スレッドではレイテンシ ≈ flush間隔分の待ち+実行時間のオーダーになるはず。
