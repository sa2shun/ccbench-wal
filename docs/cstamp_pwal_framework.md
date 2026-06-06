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
| `async_global_lsn_prefix` | true global LSN prefix baseline | safe but conservative | `completed[LSN]` bitmap から `durable_global_lsn` を前進させ、`durable_global_lsn >= LSN(T)` で ack |
| `async_global_prefix_lsn` | async pipeline + global prefix | safe but conservative | 同じ async pipeline で global durable frontier snapshot を待つ |
| `async_local_only_lsn` | async local-only upper bound | unsafe | 同じ async pipeline で自分の shard だけ待つ |
| `async_dep_frontier_lsn` | async dependency frontier baseline | synthetic 条件では safe | dependency frontier は使うが cstamp と global LSN は別々に取る |
| `async_dep_frontier_cstamp` | 提案方式の no-CC skeleton | synthetic 条件では safe | cstamp を logical LSN とし、worker / flusher / committer を分離 |

`pwal_group_commit_no_prefix` は必ず unsafe upper bound として扱う。
一般 workload では、別 shard の transaction に依存しているのに local log だけ durable で ack を返すと、
crash recovery 後に依存先が欠ける可能性がある。
`async_local_only_lsn` も同じ理由で unsafe upper bound である。

旧名 `cstamp_pwal_async_dep_frontier` は互換用 alias として残しているが、
新しい実験では `async_dep_frontier_cstamp` を使う。

## Cstamp-PWAL skeleton

`async_dep_frontier_cstamp` は次の分離を実装している。

| 要素 | 役割 |
|---|---|
| `cstamp` | logical LSN。serialization / recovery replay order を表す |
| `logger_id` | home WAL shard |
| `local_seq` | WAL shard 内の physical log position |
| `dep` | ack に必要な per-shard durable frontier |
| `durable_seq[logger_id]` | flusher が `fdatasync` 完了後に更新する local durable point |

`async_dep_frontier_lsn` は、これに加えて separate global LSN を atomic allocate する。
つまり no-CC ablation では次を比較できる。

| mode | durable condition | order id | pipeline |
|---|---|---|---|
| `pwal_group_dep_frontier` | dependency frontier | separate LSN 相当 | worker-wait |
| `async_global_lsn_prefix` | true global LSN prefix | separate LSN | async |
| `async_global_prefix_lsn` | global prefix | separate LSN | async |
| `async_local_only_lsn` | local only | separate LSN | async |
| `async_dep_frontier_lsn` | dependency frontier | separate LSN | async |
| `async_dep_frontier_cstamp` | dependency frontier | cstamp as logical LSN | async |

worker は log record を作り、synthetic dependency frontier を作り、WAL queue に enqueue する。
その後、worker は `fdatasync` や durable wait を直接待たず、次の transaction 生成に進む。

flusher は shard ごとの queue を batch 化して `write + fdatasync` し、
`durable_seq` を更新する。

committer は pending transaction 全体を周期 scan しない。
各 transaction の ack 条件を per-shard waitlist に登録し、
flusher が `durable_seq` を進めた event で該当 shard の waitlist だけを起こす。

transaction T の必要条件は次の `req` vector として登録する。

```text
req[T.log_id] = max(req[T.log_id], T.local_seq)
req[i]        = max(req[i], T.dep[i])

ack condition:
  for all shard i:
    durable_seq[i] >= req[i]
```

実装上は、各 shard に `need local_seq` 順の min-heap waitlist を持つ。
flusher が `durable_seq[i]` を進めると、`need <= durable_seq[i]` の transaction だけを pop し、
その transaction の remaining condition を減らす。
remaining が 0 になった transaction は ready queue に入り、committer が durable ack 済みにする。

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

## Actual KV dependency

`tools/kv_dep_wal_microbench.cc` は、synthetic dependency ではなく、
record metadata から actual read/write dependency frontier を作る。

各 record は次を持つ。

```text
read_frontier
write_frontier
```

transaction は read 時に `record.write_frontier` を merge し、
write 時に `record.write_frontier` と `record.read_frontier` を merge する。
commit で local log position が決まった後、
`closed_frontier = dep; closed_frontier[log_id] = local_seq` とし、
read/write set の record frontier に publish する。

これは ERMIA 接続前の小さい actual-dependency workload であり、
依存関係を作り物の確率だけにしないための中間段階である。

## 主要 counter

| counter | 意味 |
|---|---|
| `logical_commits` | worker が log enqueue まで進んだ transaction 数 |
| `acked_commits` | durability 条件を満たして ack できた transaction 数 |
| `logical_minus_acked` | enqueue 済みだが durable ack 未完了の transaction 数 |
| `logical_throughput_tps` | worker enqueue throughput |
| `acked_throughput_tps` | durable ack throughput。論文の main metric |
| `payload_build_ns` | log payload 作成時間 |
| `cstamp_alloc_ns` | cstamp atomic allocation 時間 |
| `lsn_alloc_ns` | separate global LSN allocation 時間 |
| `global_atomic_count` | cstamp / LSN の global atomic allocation 回数 |
| `mutex_wait_ns` | WAL queue / single WAL mutex 待ち |
| `write_ns` | `write` system call 時間 |
| `fdatasync_ns` | `fdatasync` 時間 |
| `prefix_wait_ns` | worker が durable 条件で待った時間 |
| `self_durable_wait_ns` | local shard durable 待ち |
| `dependency_wait_ns` | dependency frontier durable 待ち |
| `committer_queue_wait_ns` | async mode で enqueue から committer ack までの時間 |
| `committer_scan_ns` | 旧 scan 型 committer の scan 時間。event-driven では 0 になる |
| `committer_event_ns` | waitlist 登録 / durable advance event 処理時間 |
| `worker_stall_ns` | async mode で `max_inflight` に達して worker が止まった時間 |
| `dep_edges` | synthetic dependency edge 数 |
| `dep_frontier_bytes` | frontier metadata bytes |
| `waitlist_registrations` | waitlist に登録された durable condition 数 |
| `waitlist_pops` | durable advance で解決された condition 数 |
| `ready_queue_pushes` | ready queue に入った transaction 数 |
| `max_ready_queue_len` | ready queue 最大長 |
| `max_waitlist_len` | shard waitlist 最大長 |
| `max_pending_len` | pending transaction 最大数 |
| `waiting_conditions` | waitlist に登録された condition 数 |

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

straggler + async ablation dependency probability sweep を取る。

```bash
CSTAMP_PWAL_SECONDS=5 \
CSTAMP_PWAL_REPEATS=5 \
CSTAMP_PWAL_THREAD_NUM=32 \
CSTAMP_PWAL_LOGGER_NUM=4 \
CSTAMP_PWAL_PREALLOC_MB=64 \
CSTAMP_PWAL_STRAGGLER_LOGGER=3 \
CSTAMP_PWAL_STRAGGLER_SLEEP_US=1000 \
python3 scripts/run_cstamp_pwal_dep_sweep.py
```

deterministic correctness test を実行する。

```bash
g++ -O2 -std=c++17 tools/cstamp_pwal_correctness_test.cc \
  -o build/cstamp_pwal_correctness_test.exe
./build/cstamp_pwal_correctness_test.exe
```

actual KV dependency sweep を取る。

```bash
KV_DEP_SECONDS=5 \
KV_DEP_REPEATS=5 \
KV_DEP_THREAD_NUM=32 \
KV_DEP_LOGGER_NUM=4 \
KV_DEP_KEYS_PER_LOGGER=1024 \
python3 scripts/run_kv_dep_wal_microbench.py
```

actual KV 上で worker-wait と async pipeline を比較する。

```bash
KV_DEP_SECONDS=5 \
KV_DEP_REPEATS=5 \
KV_DEP_LOGGER_NUM=4 \
KV_DEP_REMOTE_READ_PROB_PPM=100000 \
python3 scripts/run_kv_dep_pipeline_sweep.py
```

async pipeline の `max_inflight` sweep を取る。

```bash
CSTAMP_PWAL_SECONDS=1 \
CSTAMP_PWAL_REPEATS=1 \
CSTAMP_PWAL_THREAD_NUM=32 \
CSTAMP_PWAL_LOGGER_NUM=4 \
CSTAMP_PWAL_DEP_PROB_PPM=100000 \
CSTAMP_PWAL_STRAGGLER_LOGGER=3 \
CSTAMP_PWAL_STRAGGLER_SLEEP_US=1000 \
python3 scripts/run_cstamp_pwal_inflight_sweep.py
```

I/O-light 条件で cstamp と separate LSN の atomic cost を見る。
これは durability correctness 用の条件ではなく、fdatasync bottleneck を外して
global atomic allocation cost を見やすくするための条件である。

```bash
CSTAMP_PWAL_SECONDS=2 \
CSTAMP_PWAL_REPEATS=3 \
CSTAMP_PWAL_THREAD_NUM=32 \
CSTAMP_PWAL_LOGGER_NUM=4 \
CSTAMP_PWAL_GROUP_SIZE=64 \
CSTAMP_PWAL_FLUSH_US=1000 \
CSTAMP_PWAL_SKIP_FDATASYNC=1 \
python3 scripts/run_cstamp_pwal_dep_sweep.py
```

dependency sweep は次を出力する。

- CSV
- Markdown report
- throughput SVG
- p99 latency SVG
inflight sweep は上記に加えて Pareto SVG も出力する。

## Smoke result: async ablation

2026-06-06 13:17 JST に、8 threads / 4 loggers / 1 second /
`straggler_logger=3` / `straggler_sleep_us=1000` の軽量 sweep を実行した。
これは論文用の最終値ではなく、実装と出力形式の確認である。

| mode | dep=0 tps | dep=0.10 tps | dep=1.00 tps |
|---|---:|---:|---:|
| `async_global_prefix_lsn` | 332011 | 345648 | 382169 |
| `async_local_only_lsn` | 540631 | 553861 | 528920 |
| `async_dep_frontier_lsn` | 527440 | 497304 | 347806 |
| `async_dep_frontier_cstamp` | 526408 | 496032 | 356498 |

この表は、すべて同じ async pipeline 上での比較である。
そのため、worker/flusher/committer 分離の効果ではなく、
durable condition と cstamp/LSN 統合の差を見るための smoke result である。

local-only は同じ async pipeline 上の unsafe upper bound である。
dependency frontier は dep_prob が低いと local-only に近く、
dep_prob が 1.0 に近づくほど global-prefix 側へ近づく。
`async_dep_frontier_lsn` は global atomic allocation が 2 回/tx、
`async_dep_frontier_cstamp` は 1 回/tx であり、表の `atomic/tx` で確認できる。

生成物:

```text
results/cstamp_pwal_dep_sweep_20260606_131744/
```

同条件で `async_dep_frontier_cstamp` の `max_inflight` sweep も実行した。

| max_inflight | ack tps | p99 us | queue_wait_us/tx | max_pending |
|---:|---:|---:|---:|---:|
| 1 | 27579 | 1242 | 282.5 | 9 |
| 2 | 48589 | 1340 | 318.5 | 17 |
| 4 | 98902 | 1250 | 307.0 | 33 |
| 8 | 128125 | 2464 | 482.8 | 65 |
| 16 | 193909 | 2591 | 638.5 | 129 |
| 32 | 335635 | 2648 | 724.8 | 257 |
| 64 | 423314 | 2838 | 1118.8 | 513 |

生成物:

```text
results/cstamp_pwal_inflight_sweep_20260606_131836/
```

## Smoke result: correctness

2026-06-06 13:40 JST に deterministic correctness test を実行した。

| mode | independent reversed flush | dependent reversed flush | acked recovered | dependency violation |
|---|---|---|---|---|
| local-only | pass | fail | no | yes |
| global-prefix | pass | pass | yes | no |
| dep-frontier-lsn | pass | pass | yes | no |
| dep-frontier-cstamp | pass | pass | yes | no |

この表の意味は、local-only は依存先 U が durable でない時点でも T に ack を返せるため unsafe であり、
global prefix / dependency frontier / cstamp frontier は reversed physical flush order でも安全ということ。

## Smoke result: actual KV dependency

2026-06-06 13:41 JST に、4 threads / 2 loggers / 1 second の actual KV dependency sweep を実行した。

| mode | remote=0 | remote=0.10 | remote=1.00 |
|---|---:|---:|---:|
| `async_global_lsn_prefix` | 372320 | 438537 | 384287 |
| `async_local_only_lsn` | 548400 | 563160 | 530402 |
| `async_dep_frontier_lsn` | 591865 | 507835 | 445498 |
| `async_dep_frontier_cstamp` | 575891 | 564791 | 522448 |

生成物:

```text
results/kv_dep_wal_microbench_20260606_134105/
```

2026-06-06 13:44 JST に、actual KV 上で worker-wait と async pipeline の軽量 thread sweep を実行した。

| mode | 1 | 2 | 4 | 8 | 16 | 32 |
|---|---:|---:|---:|---:|---:|---:|
| `pwal_group_dep_frontier` | 5574 | 11010 | 21652 | 42829 | 85250 | 550567 |
| `async_dep_frontier_lsn` | 534927 | 454805 | 585792 | 426183 | 342099 | 359282 |
| `async_dep_frontier_cstamp` | 575085 | 491604 | 623891 | 528885 | 373296 | 361334 |

この smoke は最終値ではない。
ただし actual read/write dependency から frontier を作り、
`async_dep_frontier_lsn` は `atomic/tx=2`、
`async_dep_frontier_cstamp` は `atomic/tx=1` として測れることを確認している。

生成物:

```text
results/kv_dep_pipeline_sweep_20260606_134414/
```

## 現在の範囲

まだ ERMIA/SSN 本体の read/write dependency とは接続していない。
現在の dependency frontier は no-CC synthetic dependency である。

crash injection / recovery correctness test はまだ入っていない。
そのため、現時点では durability ack 条件と metrics の実験基盤であり、
recovery theorem の end-to-end 検証ではない。

`pwal_group_commit` の global prefix は、logger shard の `min(durable_seq)` を待つ実装である。
これは保守的 baseline として使う。
