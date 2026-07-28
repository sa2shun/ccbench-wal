# ermia_ayame Step 1 実装メモ (Cstamp / P-WAL LSN の統合)

## 目的 (Ayame 最適化ポイント1)
ermia_pwal はグローバルカウンタが2本ある:
- `Lsn`: cstamp採番 (`cstamp_ = ++Lsn`、トランザクションごとに1回)
- `PwalLsnCounter`: ログレコードLSN採番 (レコードごとに1回のfetch_add)

これを1本に統合し、「cstamp = コミットログ(END)のLSN」とする。
атomic操作は1トランザクションあたり1回のfetch_addのみになる。

## 方式: LSNブロック一括予約
ssn_parallel_commit の cstamp採番点で、write_set_ のサイズ k は確定している。
- k > 0: `base = Lsn.reserve(k+1)` で k+1 個のLSNを一括予約(fetch_addは旧値=先頭を返す)。
  Updateログは base .. base+k-1、ENDログは base+k = cstamp。
- k = 0 (read-only): 従来どおり `cstamp = ++Lsn` (ログは書かない)。

## 正しさの根拠
- cstampの一意性・単調性: fetch_addで採番順に単調、一意。SSNの要件を満たす。
- アボートで予約LSNが未使用になり欠番が生じるが、コミット条件
  commitLSN <= min(flushedLSN) は「各ワーカは自分のログをLSN順にflushする」
  ことだけに依存しており、欠番があっても保守的側に倒れるだけで安全
  (min以下のLSNを持つ実在レコードはすべて永続化済み)。
- read-onlyやアイドルワーカのflushedLSN前進(空バッファflush時にcurrentMaxまで)も
  統合カウンタの currentMax を使う。安全性の理屈は ermia_pwal と同じ。

## 制限(記録)
- cstamp_ は ERMIA 本体が uint32 のため、統合カウンタが 2^32 (約43億) LSN に達すると
  切り詰めで cstamp の一意性・単調性が壊れる(0/UINT32_MAXの番兵値衝突も起きるため、
  性能制限ではなく正しさの制限)。1txで複数LSNを消費するので ermia/ermia_pwal より
  到達が速い(実測レートで上限まで約600秒。数秒〜数十秒のベンチでは余裕をもって安全)。
  対策としてドライバ終了時に pwal_max_lsn を出力し、UINT32_MAX 以上なら
  結果無効(ERROR + 終了コード1)とするガードを入れた。

## 作業内容
1. `cc/ermia_pwal` を `cc/ermia_ayame` にコピー(ドライバは ycsb_ermia_ayame.cc)、
   CMake登録(cc/ermia_ayame/CMakeLists.txt、親のプロトコル一覧に追加)。
2. include/pwal.hh:
   - `Worker::append` を「LSNを外から渡す」形に変更(採番はしない)。
   - LsnCounterはcstamp兼用の統合カウンタとして使う(名前はそのまま)。
3. transaction.cc (ssn_parallel_commit):
   - cstamp採番を「k=write_set_.size(); k>0ならreserve(k+1)で一括予約、
     cstamp=base+k。k=0ならnext()」に変更。予約したbaseをメンバに保持。
   - loggingセクションで予約済みLSNを使ってappend。
4. include/common.hh: PwalLsnCounter を廃止し、統合カウンタは既存の `Lsn` を
   pwal::LsnCounter 型に置き換える(`++Lsn`箇所は ssn_commit にもあるので合わせる)。
5. 動作確認: YCSB-A/B/C で commit_counts_ == pwal_notified_commits、
   ermia_pwal / ermia が引き続きビルド・動作すること。

## 実装しないこと
- 最適化ポイント2(依存txのみ待機)は次ステップ。コミット条件はmin(flushedLSN)のまま。
- ssn_commit(シリアル版、YCSB経路では未使用)は最小限の互換変更のみ(統合カウンタ型に
  合わせる)で、ブロック予約は入れない。
