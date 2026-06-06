# Cstamp-PWAL 実験フレームワーク

このブランチでは、論文用アルゴリズムを ERMIA 本体へ接続する前段階として、
`tools/no_cc_wal_microbench.cc` に no-CC の durability 実験基盤を追加した。

目的は、transaction execution / Masstree / SSN validation をいったん外し、
WAL durability protocol だけで次の比較ができる状態を作ることである。

- centralized WAL baseline
- P-WAL + global durable prefix
- local-durable only unsafe upper bound
- dependency frontier
- cstamp-as-logical-LSN + worker/flusher/committer pipeline

## 実装した mode

| mode | 位置づけ | 安全性 | 実装内容 |
|---|---|---|---|
| `no_durability` | durability なし上限 | crash durability なし | log record を作るだけ |
| `single_wal` | naive centralized WAL | safe | global mutex の内側で `write + fdatasync` |
| `single_wal_group_commit` | fair centralized WAL baseline | safe | shared log queue に enqueue し、1 flusher が batch `fdatasync` |
| `pwal_per_txn_fdatasync` | P-WAL naive baseline | safe | worker ごとの WAL file に毎 transaction `fdatasync` |
| `pwal_group_commit` | P-WAL + group commit + global prefix | safe but conservative | logger shard ごとに batch flush し、全 shard の durable prefix を待つ |
| `pwal_group_commit_no_prefix` | local-durable only upper bound | unsafe | 自分の logger shard だけ durable なら ack |
| `pwal_group_dep_frontier` | dependency frontier baseline | synthetic 条件では safe | 自分の log と synthetic dependency frontier の durable を worker が待つ |
| `cstamp_pwal_async_dep_frontier` | 提案方式の no-CC skeleton | synthetic 条件では safe | cstamp を logical LSN とし、worker / flusher / committer を分離 |

`pwal_group_commit_no_prefix` は必ず unsafe upper bound として扱う。
一般 workload では、別 shard の transaction に依存しているのに local log だけ durable で ack を返すと、
crash recovery 後に依存先が欠ける可能性がある。

## Cstamp-PWAL skeleton

`cstamp_pwal_async_dep_frontier` は次の分離を実装している。

| 要素 | 役割 |
|---|---|
| `cstamp` | logical LSN。serialization / recovery replay order を表す |
| `logger_id` | home WAL shard |
| `local_seq` | WAL shard 内の physical log position |
| `dep` | ack に必要な per-shard durable frontier |
| `durable_seq[logger_id]` | flusher が `fdatasync` 完了後に更新する local durable point |

worker は log record を作り、synthetic dependency frontier を作り、WAL queue に enqueue する。
その後、worker は `fdatasync` や durable wait を直接待たず、次の transaction 生成に進む。

flusher は shard ごとの queue を batch 化して `write + fdatasync` し、
`durable_seq` を更新する。

committer は pending transaction を走査し、次の条件を満たした transaction だけを durable ack 済みにする。

```text
self log durable:
  durable_seq[T.log_id] >= T.local_seq

dependency frontier durable:
  for all shard i:
    durable_seq[i] >= T.dep[i]
```

この mode の `throughput_tps` は worker enqueue throughput ではなく、
client に返せる durable ack throughput として数えている。
enqueue 数は `logical_commits`、ack 数は `acked_commits` で出力する。

## Synthetic dependency

no-CC では本物の read/write dependency がないため、dependency は synthetic に生成する。

| option | 意味 |
|---|---|
| `--dep_prob_ppm` | transaction が cross-shard dependency を持つ確率。1000000 が 100% |
| `--dep_fanout` | dependency を読む remote shard 数 |

依存を作る transaction は、target shard が publish している frontier を merge する。
これにより、transitive dependency frontier を近似する。

## 主要 counter

| counter | 意味 |
|---|---|
| `logical_commits` | worker が log enqueue まで進んだ transaction 数 |
| `acked_commits` | durability 条件を満たして ack できた transaction 数 |
| `payload_build_ns` | log payload 作成時間 |
| `cstamp_alloc_ns` | cstamp atomic allocation 時間 |
| `mutex_wait_ns` | WAL queue / single WAL mutex 待ち |
| `write_ns` | `write` system call 時間 |
| `fdatasync_ns` | `fdatasync` 時間 |
| `prefix_wait_ns` | worker が durable 条件で待った時間 |
| `self_durable_wait_ns` | local shard durable 待ち |
| `dependency_wait_ns` | dependency frontier durable 待ち |
| `committer_queue_wait_ns` | async mode で enqueue から committer ack までの時間 |
| `committer_scan_ns` | pending queue scan 時間 |
| `worker_stall_ns` | async mode で `max_inflight` に達して worker が止まった時間 |
| `dep_edges` | synthetic dependency edge 数 |
| `dep_frontier_bytes` | frontier metadata bytes |

## 実行方法

全 thread 数で baseline をまとめて取る。

```bash
NO_CC_WAL_SECONDS=2 \
NO_CC_WAL_REPEATS=1 \
NO_CC_WAL_PREALLOC_ADAPTIVE=1 \
python3 scripts/run_no_cc_wal_microbench.py
```

dependency probability sweep を取る。

```bash
CSTAMP_PWAL_SECONDS=1 \
CSTAMP_PWAL_REPEATS=1 \
CSTAMP_PWAL_THREAD_NUM=32 \
CSTAMP_PWAL_LOGGER_NUM=4 \
CSTAMP_PWAL_PREALLOC_MB=64 \
python3 scripts/run_cstamp_pwal_dep_sweep.py
```

dependency sweep は次を出力する。

- CSV
- Markdown report
- throughput SVG
- p99 latency SVG

## Smoke result

2026-06-06 12:36 JST に、8 threads / 2 loggers / 1 second の軽量 sweep を実行した。
これは論文用の最終値ではなく、実装と出力形式の確認である。

| mode | dep=0 tps | dep=0.10 tps | dep=1.00 tps |
|---|---:|---:|---:|
| `pwal_group_commit` | 33382 | 32800 | 33318 |
| `pwal_group_commit_no_prefix` | 33056 | 33297 | 33148 |
| `pwal_group_dep_frontier` | 33298 | 33104 | 33106 |
| `cstamp_pwal_async_dep_frontier` | 21957 | 20619 | 20104 |

この軽量条件では、async 版はまだ committer queue / scan overhead が大きく、
既存 group-commit worker-wait 版より遅い。
これは提案方式の完成性能ではなく、cstamp logical LSN、dependency frontier、
durable ack throughput 計測を同じ枠組みで走らせるための skeleton である。

生成物:

```text
results/cstamp_pwal_dep_sweep_20260606_123655/
```

## 現在の範囲

まだ ERMIA/SSN 本体の read/write dependency とは接続していない。
現在の dependency frontier は no-CC synthetic dependency である。

crash injection / recovery correctness test はまだ入っていない。
そのため、現時点では durability ack 条件と metrics の実験基盤であり、
recovery theorem の end-to-end 検証ではない。

`pwal_group_commit` の global prefix は、logger shard の `min(durable_seq)` を待つ実装である。
これは保守的 baseline として使う。

