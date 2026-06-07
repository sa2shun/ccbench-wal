# YCSB-B read-only frontier publish skip optimization

date: 2026-06-07

## 目的

YCSB-B で `async_dep_frontier_cstamp` が frontier metadata cost によって頭打ちしていたため、read-only transaction の durability frontier publish を skip する最適化を入れた。

重要な線引きは以下。

| 処理 | read-only tx での扱い |
|---|---|
| read version の `write_frontier_` collect | 残す |
| read-only response / durable ack 前の dependency wait | 残す |
| read-only tx 自身の WAL record | 省く |
| read-only tx の `closed_frontier` を read version の `read_frontier_` に publish | 省く |
| SSN/CC 用の read tracking | 残す |

read-only transaction は redo/data logging で recovery すべき DB state を作らない。そのため future writer に read-only transaction 自身を durability dependency として publish する必要はない。ただし、read-only transaction が読んだ version の writer が durable になる前に client response を返すのは危険なので、dependency frontier の collect と durable ack wait は残す。

## 実装

変更点:

- `TxExecutor::ssn_parallel_commit()` で `write_set_.empty()` かつ dependency frontier mode の場合、WAL enqueue と `publishFrontiers()` を省く。
- 代わりに `WalLogger::ackReadOnlyWithFrontier()` を呼び、read versions から集めた `dep_frontier_` が durable になった時点で async ack する。
- `wal_stats_read_only_commits` を追加し、read-only transaction の割合を出せるようにした。
- deterministic correctness test に read-only scenario を追加した。

安全性確認:

```text
R reads x written by U.
R does not publish itself to x.read_frontier.
R is not acked before U's frontier is durable.
Future writer still depends on U via x.write_frontier, but not on R.
```

`./build/ermia_cstamp_pwal_correctness_test.exe` は通過済み。

## 生成物

| artifact | path |
|---|---|
| summary CSV | `paper/tables/ycsbb_cstamp_readonly_skip_20260607.csv` |
| throughput PDF | `paper/figures/fig_ycsbb_cstamp_readonly_skip_ack_tps.pdf` |
| latency PDF | `paper/figures/fig_ycsbb_cstamp_readonly_skip_latency.pdf` |
| pending PDF | `paper/figures/fig_ycsbb_cstamp_readonly_skip_pending.pdf` |

latency 図は帯なしの折れ線のみである。

## 条件

workload は YCSB-B。

```text
ycsb_rratio = 95
ycsb_max_ope = 10
worker threads = 1,2,4,8,16,32
seconds = 3
repeats = 3
```

主に使う tuned config:

| logger_num | committer_num | group_size | flush_us |
|---:|---:|---:|---:|
| 4 | 1 | 16 | 50 |

## 結果

read-only transaction 率は約 0.599/tx だった。これは YCSB-B の `0.95^10 ≈ 0.599` と一致する。

read frontier update は、以前の約 9.5 updates/tx から約 3.5 updates/tx まで下がった。これは read-only transaction の read frontier publish を skip できていることを示す。

| worker | P-WAL tps | Cstamp before tps | Cstamp read-only skip tps | vs P-WAL | vs before | p99 us | pending |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 12,742 | 64,465 | 85,371 | 6.70x | 1.32x | 256 | 8 |
| 2 | 22,217 | 91,501 | 130,804 | 5.89x | 1.43x | 256 | 9 |
| 4 | 41,511 | 138,642 | 194,043 | 4.67x | 1.40x | 256 | 18 |
| 8 | 67,299 | 181,625 | 287,434 | 4.27x | 1.58x | 256 | 23 |
| 16 | 88,130 | 196,097 | 336,634 | 3.82x | 1.72x | 256 | 37 |
| 32 | 109,029 | 176,547 | 374,003 | 3.43x | 2.12x | 512 | 49 |

## counter breakdown

| worker | read-only commits/tx | read frontier updates/tx | publish ns/tx before | publish ns/tx after |
|---:|---:|---:|---:|---:|
| 1 | 0.598 | 3.518 | 4,016.8 | 1,466.7 |
| 2 | 0.599 | 3.506 | 6,934.8 | 2,096.8 |
| 4 | 0.599 | 3.515 | 9,766.1 | 3,163.4 |
| 8 | 0.599 | 3.513 | 16,764.1 | 4,482.3 |
| 16 | 0.599 | 3.511 | 37,095.3 | 8,366.8 |
| 32 | 0.599 | 3.514 | 100,063.8 | 14,776.0 |

この最適化で、以前最大の問題だった `frontier_publish_ns/tx` が大きく下がった。特に 32 worker では 100.1us/tx から 14.8us/tx まで下がっている。

## 頭打ちは解けたか

以前の tuned config は以下だった。

```text
1:  64K
2:  92K
4: 139K
8: 182K
16:196K
32:177K
```

read-only skip 後は以下。

```text
1:  85K
2: 131K
4: 194K
8: 287K
16:337K
32:374K
```

したがって、少なくとも YCSB-B では 8 worker で頭打ちする問題は大きく改善した。32 worker まで伸びている。

ただし 16 -> 32 の伸びは小さくなっており、次の bottleneck は残っている。after optimization でも 32 worker では `frontier_publish_ns/tx` が 14.8us、`frontier_collect_ns/tx` も増えているため、frontier metadata はまだ重要な最適化対象である。

## latency の読み方

read-only skip 後の tuned Cstamp-PWAL は、

- 1-8 worker: p99 = 256us
- 16 worker: p99 = 256us
- 32 worker: p99 = 512us

P-WAL は低スレッドでは 128us なので、1-8 worker の absolute p99 latency はまだ P-WAL の方が良い。一方で 16 worker では Cstamp が P-WAL と同等、32 worker では同等で、throughput は Cstamp が 3.43x 高い。

## 論文上の主張

この最適化は新しい protocol というより、redo/data logging 前提で自然な read-only handling である。

書き方:

> Read-only transactions do not create recoverable database state under redo logging. Cstamp-PWAL therefore collects the durability frontiers of versions read by a read-only transaction and waits for them before returning results, but does not publish the read-only transaction as a durability dependency to future writers.

日本語では、

> read-only transaction は redo すべき DB state を作らないため、future writer に durability dependency として publish しない。ただし、読んだ version の writer が durable になるまでは response を返さない。

## 最終判断

今回できる範囲で最も効いた最適化は read-only frontier publish skip だった。

効果:

1. P-WAL に全 worker 数で throughput 勝ち。
2. 8 worker での頭打ちは解消傾向。
3. 32 worker で 374K tx/s、P-WAL 比 3.43x。
4. pending は 50 前後で安定。
5. read frontier update は 9.5/tx から 3.5/tx に低下。
6. 32 worker の frontier publish cost は 100us/tx から 15us/tx に低下。

残る課題:

- 低スレッド absolute p99 latency は P-WAL よりまだ悪い。
- 16 -> 32 worker の scaling は鈍い。
- read-write transaction では依然として frontier publish/collect が残る。
