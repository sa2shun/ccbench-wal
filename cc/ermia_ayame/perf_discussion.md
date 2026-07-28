# Ayame 性能分析データまとめ (2026-07-26)

対象: ERMIA(SSN MVCC) + P-WAL統合の研究実装 (ccbench/cc/ermia_ayame)。
Ayame = 最適化1(cstampとWAL LSNの単一カウンタ統合、LSNブロック一括予約) +
最適化2(コミット通知をmin(flushedLSN)全ワーカ待ちから依存先のみ待ちに変更。
依存条件 = 自ワーカflushedLSN>=自commitLSN AND 依存先ワーカのconfirmedLSN(通知済み
最大コミットLSN)>=依存LSN。通知判定はワーカ自身のflush直後(pwal_flush_ntxコミット毎)のみ実行)。

環境: Xeon Gold 5418N x2 (96論理コア, 2 NUMA), ext4/LVM(HDDかSSDかは未確認), 96台構成。
YCSB 100万件, 10 ops/tx, Zipf 0。ログレコード32B固定。

## 1. スレッドスケーリング (flush_ntx=10, YCSB-A=read50%)
- ermia(ロギングなし): 96th 2.3-4.2M tps
- ermia_pwal / ermia_ayame: 両者ともスループット頭打ち ~550-570k tps (32th以降ほぼ飽和)
- レイテンシp50 (同一実行): pwal 658µs(8th)/1568(32th)/4325(96th)
  ayame 276µs(8th)/570(32th)/1682(96th) → 2.4-2.8倍改善、ただし96thでも増加は残る
- YCSB-C(read100%)は3系統同等 (~5-5.9M tps @96th)

## 2. syscallプロファイル (strace -c, ayame 8th YCSB-A 2s)
- fdatasync: 時間の56.5%, 46,726回, 平均127µs/回
- write: 15%, 33µs/回
- その他は起動時のもの

## 3. fdatasyncマイクロベンチ (同一ストレージ, 4KB, 過去測定)
- 並行スレッド数とfdatasync平均レイテンシ: 1th 50µs → 8th 168µs → 32th 341µs
- fdatasync ops/s: 1th 19k → 32th 92k (sync_each)。バッチ書き(8回write毎sync)では
  4th以降 ~220k write-ops/s で飽和、syncレイテンシは32thで1.1ms

## 4. flush間隔(pwal_flush_ntx)スイープ (32th, YCSB-A, 2s, 背景負荷あり参考値)
| ntx | ayame tps | ayame p50 | ayame p99 | pwal tps | pwal p50 |
|---|---|---|---|---|---|
| 1   | 80k  | 382µs | 626µs | - | - |
| 5   | 328k | 420µs | 814µs | - | - |
| 10  | 503k | 548µs | 1.3ms | ~475k | 1.5ms |
| 50  | 1.10M | 428ms(!) | 873ms | 1.12M | 2.9ms |
| 200 | 1.69M | 841ms(!) | 1.65s | 1.79M | 6.0ms |

重要な観察: ntx>=50でayameのレイテンシが爆発し(pwalの100倍以上悪い)、
2秒の実行終了時点で通知待ちの約半数が滞留していた(サンプル1.2M/通知2.26M)。
仮説: 依存条件が「依存先のconfirmedLSN(=通知済み)」であり、通知判定は各ワーカの
flush直後にしか走らないため、依存の確定がワーカ間をカスケードする際に
1ホップあたり最大flush間隔分(N/コミットレート)の遅延がかかる。Nが大きいと
確定フロンティアの進行がコミットレートに追いつかず、キューが成長し続ける。

## 5. その他の既知事項
- グローバルは統合LSNカウンタ1本(fetch_add/tx 1回)。通知判定の全ワーカ走査は廃止済み。
- WALバッファ/ファイル/コミットキュー/レイテンシ配列はワーカローカル。
  flushedLSN/confirmedLSNのみ他ワーカから読まれる(alignas 64)。
- スレッドはsetThreadAffinityでピン止め。NUMA 2ノード。WALファイルは全て同一ext4。
- cstampはERMIA本体制約でuint32(2^32 LSNで測定無効ガードあり)。

## 追記(2026-07-27): 正式測定の要約
- 静音環境・10s×3試行。YCSB-A 96th: pwal p50 4,655µs (flushwait 2,211 + depwait 2,609)
  vs ayame p50 2,234µs (flushwait 2,226 + depwait 5)。tpsは両者~430kで同等(fdatasync律速)。
- ボトルネック定量化: fetch_add 96th競合で3.7µs/回(容量25.8M/s、現行消費3M/s以下で非律速)。
  96ライン走査は95writer下で9.2µs/回。fdatasync 96並行で1.9ms。
- 修正済み: 通知判定は毎コミット(flush間隔を大きくしてもレイテンシ発散しない)。
  readerビットマップ128bit化(96th結果は有効)。
- ワークロード注意: ccbench流YCSB(一様分布, VAL_SIZE=4B, 10ops/tx, blind write)。

## 追記2(最新状態): 3役割分離+バックプレッシャー後の全体像
- モード: self(ワーカ自己flush+毎コミット通知判定) / pipeline(worker+flusher(2)+committer(1))。
  pipelineは -pwal_backpressure=K で未確定tx数を制限(待機はsched_yield)。
- 96th YCSB-Aの到達点: self 433k tps/p50 2.2ms。pipeline K=32: 310k/8.6ms、
  K=128: 757k/14.4ms、K=512: 1.35M/34ms、K=∞: 3.83M/5.2s(非定常発散)。
  上限の階段: K=∞ 3.83M → tmpfs 4.02M → ermia素 4.49M。
- P-WAL(min待ち)との比較: 実用K域(8-128)でayameが両軸優位(depwait差)。K>=512で収束。
- 既知の問題/限界:
  (a) selfモードは依存密度(zipf>=0.6,rmw)やN>=50で通知が逐次化し発散
      (通知条件が「依存先の通知済みconfirmedLSN」のため。根本対策=flushedLSN+推移的依存伝播、未実装)
  (b) pipelineのレイテンシ床5-6ms(K=8でも)はflusher(96ワーカ/2本の巡回)+committer巡回の遅延
  (c) K固定はワーカ数に対して相対的(少スレッド×大Kで確定行列が深くなり高レイテンシ)
  (d) Kが効く領域でスレッド増→tps低下が残存(yield化で改善したが解消せず。
      巡回時間がワーカ数比例で伸びる+96ファイルへの細切れfdatasyncのjbd2コスト増が残r因)
  (e) RO即時通知のrecoverability単純化、依存リストのログ未記録(リカバリ未実装)
  (f) cstamp uint32(2^32 LSNで測定無効。tmpfs級レートでは3-4分)

## 追記3: worker/flusher/committer比率の設計問題
現状: worker W個(コアにピン留め)、flusher F個(既定2)、committer C=1(実装上1固定:
統計とconfirmedLSNの単一書き手規約のため)。合計>96コアでオーバーサブスクリプション。
クイック測定(5s×1、実行順に自己負荷蓄積のバイアスあり、ayame/YCSB-A):
- K=32:  F=1: 251k/11.6ms, F=2: 306-313k/9.4-9.5ms, F=4: 391k/7.1ms, F=8: 468k/5.6ms
- K=512: F=1: 1.47M, F=2: 1.53-1.73M, F=4: 1.56M, F=8: 1.64M (順序バイアス大)
- K=2048: 同様にF=8が後半ハンデ込みで1.94M
既知の構造:
- flusher巡回周期 R_f ≈ (W/F) × (t_write(batch)+t_sync(F))。t_syncは並行flusher数に依存
  (fdatasyncマイクロベンチ: 1並行50µs→32並行341µs、集約~93k syncs/s)。
- レイテンシの床 ≈ R_f + committer巡回。K小域ではR_fが支配的。
- committerの仕事: 1エントリ確定 ~100-200ns + per-worker mutex。2M tps時でも~0.4コア。
- workerはW×コミットレート、ストレージ律速下ではWの限界効用は小さい。
