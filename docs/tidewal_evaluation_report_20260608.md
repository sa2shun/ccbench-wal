# TideWAL 評価レポート

日付: 2026-06-08

このレポートは、論文評価節で使う実験結果を 3 system に絞ってまとめたものです。
component ablation は含めません。比較対象は以下の 3 本だけです。

- Single WAL
- P-WAL
- TideWAL

内部分析は、counter と perf で「なぜこの 3 本の結果になるか」を説明する目的で使います。

## 実験条件

| item | value |
|---|---|
| binary | `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` |
| workloads | YCSB-A, YCSB-B, YCSB-C |
| worker threads | 1, 2, 4, 8, 16, 32 |
| repeats | 5 |
| run time | 5 sec / run |
| read-only WAL skip | enabled for all systems |
| TideWAL group_size | 16 |
| TideWAL flush_us | 50 |
| TideWAL max_pending | 65536 |
| TideWAL committer | 1 |
| TideWAL logger mapping | 1->1, 2->1, 4->1, 8->2, 16->4, 32->7 |

横軸は transaction worker threads です。TideWAL は追加で flusher/logger threads と committer thread を使うため、表では total active threads も明記しています。

## 出力ファイル

Main benchmark:

- `paper/tables/ycsbabc_tidewal_worker_threads_20260608.csv`
- `docs/ycsbabc_tidewal_worker_threads_20260608.md`
- `paper/figures/fig_ycsbabc_worker_threads_throughput.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_latency.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_pending.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_tidewal_speedup_vs_pwal.pdf`

Perf:

- `paper/tables/tidewal_perf_stat_20260608.csv`
- `paper/tables/tidewal_perf_top_20260608.csv`
- `docs/tidewal_perf_eval_20260608.md`

## 32-worker summary

| workload | system | workers | WAL streams | flushers | committers | total active | ack tps | p99 us | pending | fdatasync count | commits/fdatasync | read-only ratio | frontier bytes/tx | WAL atomic/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 32 | 1 | 0 | 0 | 32 | 10,082 | 4,096 | 0 | 40,320 | 1.00 | 0.001 | 0.0 | 6.005 |
| YCSB-A | P-WAL | 32 | 32 | 0 | 0 | 32 | 70,360 | 512 | 0 | 281,205 | 1.00 | 0.001 | 0.0 | 5.999 |
| YCSB-A | TideWAL | 32 | 7 | 7 | 1 | 40 | 303,272 | 2,048 | 221 | 84,045 | 14.44 | 0.001 | 56.0 | 0.000 |
| YCSB-B | Single WAL | 32 | 1 | 0 | 0 | 32 | 28,489 | 2,048 | 0 | 45,696 | 2.49 | 0.599 | 0.0 | 0.901 |
| YCSB-B | P-WAL | 32 | 32 | 0 | 0 | 32 | 251,575 | 256 | 0 | 403,855 | 2.49 | 0.599 | 0.0 | 0.901 |
| YCSB-B | TideWAL | 32 | 7 | 7 | 1 | 40 | 460,460 | 512 | 59 | 130,210 | 14.15 | 0.599 | 56.0 | 0.000 |
| YCSB-C | Single WAL | 32 | 1 | 0 | 0 | 32 | 1,465,872 | 22 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | P-WAL | 32 | 32 | 0 | 0 | 32 | 1,457,167 | 22 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | TideWAL | 32 | 7 | 7 | 1 | 40 | 1,387,033 | 23 | 0 | 0 | 0.00 | 1.000 | 56.0 | 0.000 |

## 読み方

YCSB-A/B では TideWAL が P-WAL より高い durable ack throughput を出します。

- YCSB-A: 303K vs 70K, 4.3x over P-WAL
- YCSB-B: 460K vs 252K, 1.8x over P-WAL

主因は fdatasync batching です。P-WAL は WAL stream を分けますが、32 workers では小さい flush が非常に多くなります。TideWAL は group flusher により commits/fdatasync を 14 前後まで増やします。

| workload | system | fdatasync count | commits/fdatasync |
|---|---|---:|---:|
| YCSB-A | P-WAL | 281,205 | 1.00 |
| YCSB-A | TideWAL | 84,045 | 14.44 |
| YCSB-B | P-WAL | 403,855 | 2.49 |
| YCSB-B | TideWAL | 130,210 | 14.15 |

したがって、P-WAL のボトルネックは「parallel log stream はあるが、小さい fdatasync が多い」ことです。
TideWAL ではこの I/O 同期圧力が下がり、残るボトルネックは frontier metadata と queue management に移ります。

## perf summary

perf は 32 workers / 5 sec / 3 repeats で取りました。

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | context switches |
|---|---|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 9,759 | 1.20 | 306,558 | 634,424 | 127,682 |
| YCSB-A | P-WAL | 69,941 | 18.36 | 662,955 | 392,300 | 1,418,583 |
| YCSB-A | TideWAL | 302,880 | 24.15 | 196,890 | 141,190 | 3,290,211 |
| YCSB-B | Single WAL | 27,139 | 1.08 | 99,466 | 171,821 | 136,635 |
| YCSB-B | P-WAL | 255,003 | 16.45 | 162,643 | 103,787 | 1,394,703 |
| YCSB-B | TideWAL | 463,454 | 24.35 | 128,797 | 77,435 | 3,880,368 |

perf record の上位シンボルは以下です。

- P-WAL: `native_queued_spin_lock_slowpath` が YCSB-A 39.6%, YCSB-B 31.8%
- P-WAL: `ccbench::WalLogger::logPerThread` も上位
- TideWAL: `pthread_mutex_lock`, `TxExecutor::mergeVersionFrontier`, `TxExecutor::publishReadFrontier` が上位
- YCSB-C TideWAL: `TxExecutor::mergeVersionFrontier` が 11.0%

解釈:

- Single WAL は shared log path により commit path が直列化され、CPU をあまり使えない。
- P-WAL は shared WAL insertion bottleneck を外すが、頻繁な小さい fdatasync と kernel synchronization が支配的になる。
- TideWAL は commits/fdatasync を増やし、fdatasync pressure を下げる。その代わり frontier metadata と queue management が次のコストになる。

## YCSB-C sanity

YCSB-C は read-only なので、read-only WAL skip が有効な条件では WAL persistence を測っていません。

32 workers では:

- WAL bytes = 0
- fdatasync count = 0
- pending = 0
- Single WAL / P-WAL / TideWAL はほぼ同等

TideWAL は read-only でも、読んだ version の writer が durable かどうか確認するための frontier collect を行います。
そのため YCSB-C の perf record では `TxExecutor::mergeVersionFrontier` が見えます。
これは WAL persistence の差ではなく、read-side frontier bookkeeping overhead です。

## Timestamp integration

TideWAL では WAL-side separate global LSN allocation を行いません。

32 workers の main benchmark では:

- TideWAL `WAL atomic/tx = 0.000`
- TideWAL `wal_lsn_alloc_ns_per_tx = 0`

これは「ERMIA の cstamp を logical LSN として使っているため、WAL 側の別 global LSN を取っていない」という意味です。
throughput の直接改善として強く主張するのではなく、CC の serialization order と WAL/recovery order の二重管理を消す設計上の利点として扱います。

## 論文での主張

この評価から言えることは以下です。

1. Single WAL は shared log serialization point により、durability を入れるとスケールしない。
2. P-WAL は WAL stream を分けるが、頻繁な小さい fdatasync と kernel synchronization が残る。
3. TideWAL は worker/flusher/committer separation と group flushing により commits/fdatasync を増やし、YCSB-A/B で durable ack throughput を改善する。
4. TideWAL の残りコストは frontier metadata と queue management である。
5. YCSB-C は read-only fast path sanity check であり、durability result として扱わない。
6. TideWAL は WAL-side global LSN allocation を消しているが、これは主に設計簡素化として主張する。
