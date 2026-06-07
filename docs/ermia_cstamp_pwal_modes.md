# ERMIA Cstamp-PWAL 接続メモ

このメモは、ERMIA に Cstamp-PWAL 系の最小 mode を接続した状態を記録する。

## 実装した 5 mode

| mode | 実行方法 | 意味 | 用途 |
|---|---|---|---|
| `ermia_no_durability` | `build/cc/ermia/ycsb_abort0_ermia.exe` | 既存 ERMIA。WAL なし | 上限 |
| `ermia_pwal_group_global_prefix` | `CCBENCH_WAL_DURABLE_MODE=group_global_prefix build/cc/ermia_pwal/ycsb_abort0_ermia_pwal.exe` | worker-wait + group flusher + true global LSN prefix | 旧 P-WAL 系 baseline |
| `ermia_async_global_lsn_prefix` | `CCBENCH_WAL_DURABLE_MODE=async_global_lsn_prefix build/cc/ermia_pwal/ycsb_abort0_ermia_pwal.exe` | async + true global LSN prefix | safe conservative baseline |
| `ermia_async_dep_frontier_lsn` | `CCBENCH_WAL_DURABLE_MODE=async_dep_frontier_lsn build/cc/ermia_pwal/ycsb_abort0_ermia_pwal.exe` | async + dependency frontier + separate global LSN | cstamp 統合なし baseline |
| `ermia_async_dep_frontier_cstamp` | `CCBENCH_WAL_DURABLE_MODE=async_dep_frontier_cstamp build/cc/ermia_pwal/ycsb_abort0_ermia_pwal.exe` | async + dependency frontier + cstamp logical LSN | 提案方式 |

`ermia_no_durability` 用に `ycsb_abort0_ermia.exe` を追加した。
これは `ycsb_abort0_ermia_pwal.exe` と同じ partitioned YCSB を WAL なし ERMIA で走らせる。

P-WAL 系 mode は、既存の `ermia_pwal` binary に環境変数で選ぶ WAL durable mode として入っている。

```sh
CCBENCH_WAL_DURABLE_MODE=sync
CCBENCH_WAL_DURABLE_MODE=group_global_prefix
CCBENCH_WAL_DURABLE_MODE=async_global_lsn_prefix
CCBENCH_WAL_DURABLE_MODE=async_dep_frontier_lsn
CCBENCH_WAL_DURABLE_MODE=async_dep_frontier_cstamp
```

`sync` は従来通り、worker が `write + fdatasync + global durable prefix wait` を
`logCommit()` 内で待つ。

`group_global_prefix` は、worker が WAL shard queue に enqueue し、flusher が batch
write + `fdatasync` する。ただし worker は true global LSN prefix が自分の commit LSN に
届くまで待つ。

`async_global_lsn_prefix` は、worker が WAL record を shard queue に enqueue したら
`logCommit()` から戻る。各 shard の flusher thread が batch write と `fdatasync` を行い、
すべての global LSN が prefix として durable になった transaction だけを ack 済みにする。

`async_dep_frontier_lsn` は、ack 条件を true global prefix ではなく
dependency-closed frontier にする。ただし WAL/recovery 用の separate global LSN allocation は
残す。

`async_dep_frontier_cstamp` は、`async_dep_frontier_lsn` から separate global LSN allocation を
外し、ERMIA の `cstamp` を logical LSN として使う。

## true global LSN prefix の意味

各 log record と commit record に separate global LSN を割り当てる。

transaction `T` の commit LSN を `g(T)` とすると、ack 条件は次である。

```text
durable_global_lsn >= g(T)
```

`durable_global_lsn` は completion bitmap から前から連続して durable な LSN だけを進める。
したがって、一部 shard の log が遅れると、それより後ろの LSN を持つ transaction は
別 shard で durable 済みでも ack されない。

これは `global LSN prefix` baseline として使える。

## Dependency frontier の実装

`cc/ermia_pwal/include/version.hh` の各 Version に、次の 2 つを追加した。

```text
write_frontier
read_frontier
```

実体は `shared_ptr<const WalFrontier>` で、初期 version は `nullptr` のままにしてメモリを節約する。
commit 済み version にだけ frontier pointer を publish する。

read 時:

```text
T.dep = max(T.dep, version.write_frontier)
```

write/delete 時:

```text
T.dep = max(T.dep, overwritten_version.write_frontier)
T.dep = max(T.dep, overwritten_version.read_frontier)
```

commit 時:

```text
WAL enqueue -> local_seq 取得
closed = T.dep
closed[T.log_id] = max(closed[T.log_id], local_seq)
new written versions: write_frontier = closed
read versions: read_frontier = max(read_frontier, closed)
```

この publish は version を `committed/deleted` にする前に行う。
つまり、visible な新 version には frontier が先に入っている。

## Logger 数と pending 上限

queued mode では worker 数と WAL shard 数を分けられる。

```sh
CCBENCH_WAL_LOGGER_NUM=4
```

worker `thid` は `thid % CCBENCH_WAL_LOGGER_NUM` の WAL shard に enqueue する。

committer thread 数も環境変数で指定できる。

```sh
CCBENCH_WAL_COMMITTER_NUM=1
```

現在の async pipeline は物理的に次の 3 段に分かれている。

```text
worker
  -> WAL shard queue
flusher
  -> write + fdatasync
  -> 自分の durable event queue に push
committer
  -> durable event queue を読む
  -> global LSN prefix / dependency frontier の ack 条件を判定
  -> durable ack counter を進める
```

durable event queue は flusher shard ごとに 1 本ある。
committer は `CCBENCH_WAL_COMMITTER_NUM` 本だけ起動し、
`logger_id % committer_num` に相当する shard queue を担当する。
通常は `1` または `2` を比較すればよい。

async mode では durable ack backlog を制御するため、pending 上限を持つ。

```sh
CCBENCH_WAL_MAX_PENDING=65536
```

`0` にすると無制限になる。frontier mode は version ごとに frontier pointer を publish するので、
smoke では小さめの値にしておく方がよい。

## Frontier サイズ

dependency frontier は、以前は固定長 256 shard vector だったため、
`frontier bytes/tx` が約 2,048 bytes になっていた。

現在は `logger_num` サイズの可変長 vector にしている。
例えば `CCBENCH_WAL_LOGGER_NUM=4` なら、

```text
4 * sizeof(uint64_t) = 32 bytes / tx
```

`CCBENCH_WAL_LOGGER_NUM=8` なら 64 bytes / tx になる。

## 重要な読み方

既存 ccbench の `commit_counts_` と `throughput[tps]` は、async mode では
durable ack ではなく logical commit を数える。

論文用の main metric は次を使う。

```text
durable ack throughput = wal_stats_measured_acked_commits / actual_extime
```

補助指標として次を見る。

```text
wal_stats_measured_logical_commits
wal_stats_measured_acked_commits
wal_stats_measured_logical_minus_acked
wal_stats_measurement_pending_commits
wal_stats_shutdown_drain_acked_commits
wal_stats_max_pending_commits
wal_stats_durable_global_lsn
wal_stats_measured_durable_global_lsn
```

特に `wal_stats_measured_logical_minus_acked` は、worker がどれだけ durable ack より先に
進んだかを表す。async pipeline ではこの値が大きくなり得る。

## 実行例

no durability ERMIA:

```sh
./build/cc/ermia/ycsb_abort0_ermia.exe \
  --thread_num=2 --extime=1 --ycsb_tuple_num=1000 --ycsb_max_ope=1 --ycsb_rratio=0
```

worker-wait group global prefix:

```sh
CCBENCH_WAL_DURABLE_MODE=group_global_prefix \
CCBENCH_WAL_LOGGER_NUM=1 \
CCBENCH_WAL_GROUP_SIZE=8 \
CCBENCH_WAL_FLUSH_US=100 \
./build/cc/ermia_pwal/ycsb_abort0_ermia_pwal.exe \
  --thread_num=2 --extime=1 --ycsb_tuple_num=1000 --ycsb_max_ope=1 --ycsb_rratio=0
```

async global LSN prefix:

```sh
CCBENCH_WAL_DURABLE_MODE=async_global_lsn_prefix \
CCBENCH_WAL_LOGGER_NUM=1 \
CCBENCH_WAL_GROUP_SIZE=8 \
CCBENCH_WAL_FLUSH_US=100 \
CCBENCH_WAL_MAX_PENDING=4096 \
./build/cc/ermia_pwal/ycsb_abort0_ermia_pwal.exe \
  --thread_num=2 --extime=1 --ycsb_tuple_num=1000 --ycsb_max_ope=1 --ycsb_rratio=0
```

async dep frontier LSN:

```sh
CCBENCH_WAL_DURABLE_MODE=async_dep_frontier_lsn \
CCBENCH_WAL_LOGGER_NUM=1 \
CCBENCH_WAL_GROUP_SIZE=8 \
CCBENCH_WAL_FLUSH_US=100 \
CCBENCH_WAL_MAX_PENDING=4096 \
./build/cc/ermia_pwal/ycsb_abort0_ermia_pwal.exe \
  --thread_num=2 --extime=1 --ycsb_tuple_num=1000 --ycsb_max_ope=1 --ycsb_rratio=0
```

async dep frontier cstamp:

```sh
CCBENCH_WAL_DURABLE_MODE=async_dep_frontier_cstamp \
CCBENCH_WAL_LOGGER_NUM=1 \
CCBENCH_WAL_GROUP_SIZE=8 \
CCBENCH_WAL_FLUSH_US=100 \
CCBENCH_WAL_MAX_PENDING=4096 \
./build/cc/ermia_pwal/ycsb_abort0_ermia_pwal.exe \
  --thread_num=2 --extime=1 --ycsb_tuple_num=1000 --ycsb_max_ope=1 --ycsb_rratio=0
```

短い smoke result では、2 threads / 1 sec / abort0 write-only / 1 op/tx で次のようになった。

| mode | measured acked commits | logical commits | measured pending | fdatasync count | global atomic count |
|---|---:|---:|---:|---:|---:|
| ermia_no_durability | 2,879,043 | 2,879,043 | 0 | 0 | 0 |
| group_global_prefix | 8,932 | 8,932 | 0 | 4,467 | 17,868 |
| async_global_lsn_prefix | 63,616 | 67,712 | 4,096 | 8,465 | 135,428 |
| async_dep_frontier_lsn | 73,504 | 77,600 | 4,096 | 9,701 | 155,204 |
| async_dep_frontier_cstamp | 66,112 | 70,208 | 4,096 | 8,777 | 0 |

この表の読み方は次の通り。

`ermia_no_durability` は durability なし上限である。

`group_global_prefix` は worker が durable wait で止まるので logical と ack がほぼ一致する。

async 系は worker が先に進むため logical commit と measured ack が分かれる。
この smoke では `CCBENCH_WAL_MAX_PENDING=4096` にしているため、
測定終了時に `4,096` 件が durable ack 待ちとして残る。
終了後の drain で最終的には全件 ack されるが、論文用 throughput には drain を混ぜない。

`async_dep_frontier_cstamp` は `wal_stats_global_atomic_count = 0` であり、
WAL 側の separate global LSN allocation をしていない。

## 現時点の位置づけ

これは ERMIA 接続の最小実装であり、まだ最適化版ではない。

この段階で確認できたことは次である。

1. ERMIA の commit path から worker の `fdatasync` / prefix wait を外せる。
2. true global LSN prefix の async baseline として、completion bitmap による durable prefix を持てる。
3. actual read/write dependency から dependency frontier を作れる。
4. `async_dep_frontier_lsn` と `async_dep_frontier_cstamp` を同じ pipeline 上で比較できる。
5. logical commit と durable ack を分けて測定できる。

次の本測定では、`wal_stats_measured_acked_commits / actual_extime` を main throughput とし、
`wal_stats_measured_logical_minus_acked`、`wal_stats_measurement_pending_commits`、
`wal_stats_dep_frontier_bytes`、`wal_stats_dep_wait_conditions`、
`wal_stats_global_atomic_count` を一緒に読む。

## 論文用最小実験 runner

ERMIA 上の最小実験は次の script で回す。

```sh
python3 scripts/run_ermia_cstamp_pwal_experiments.py --experiment abort0
python3 scripts/run_ermia_cstamp_pwal_experiments.py --experiment dependency
python3 scripts/run_ermia_cstamp_pwal_experiments.py --experiment straggler
python3 scripts/run_ermia_cstamp_pwal_experiments.py --experiment cstamp
python3 scripts/run_ermia_cstamp_pwal_experiments.py --experiment ratio
python3 scripts/run_ermia_cstamp_pwal_experiments.py --experiment max_pending
```

全実験をまとめて回す場合:

```sh
python3 scripts/run_ermia_cstamp_pwal_experiments.py --experiment all
```

デフォルトは論文用の最低条件にしている。

worker/flusher/committer 比率を見る場合:

```sh
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment ratio \
  --seconds 3 \
  --repeats 3 \
  --thread-num 32 \
  --ratio-workloads abort0_write1,ycsb_b \
  --logger-nums 1,2,4,8 \
  --committer-nums 1,2 \
  --max-pending 4096
```

backlog / latency Pareto を見る場合:

```sh
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment max_pending \
  --seconds 3 \
  --repeats 3 \
  --thread-num 32 \
  --pareto-workloads ycsb_b \
  --logger-num 8 \
  --committer-num 1 \
  --max-pending-values 1024,4096,16384,65536
```

2026-06-06 の短い ratio sweep では、32 workers の条件で `logger_num=8` が
`logger_num=4` より高 throughput だった。
`committer_num` は 1 と 2 の差が小さく、まずは `committer_num=1` を基準にしてよい。

同日の max_pending sweep では、YCSB-B / 32 workers / `logger_num=8` で、
`async_global_lsn_prefix` は throughput が高い一方、`pending` がほぼ
`max_pending` に張り付き、p99 も `max_pending` に応じて悪化した。
`async_dep_frontier_cstamp` は throughput では負けるが、`pending` は約 50、
p99 は約 512 us で安定した。

```text
seconds = 5
repeats = 5
threads = 1,2,4,8,16,32
logger_num = 4
group_size = 8
flush_us = 100
max_pending = 65536
```

出力先は `results/ermia_cstamp_pwal_<timestamp>/` で、各 experiment ごとに
CSV、Markdown、throughput SVG、p99 latency SVG を出す。

### correctness test

script は最初に deterministic correctness test も実行する。
単体で実行する場合:

```sh
cmake --build build --target ermia_cstamp_pwal_correctness_test.exe -j2
./build/ermia_cstamp_pwal_correctness_test.exe
```

期待出力:

```text
local-only: dependent reversed flush で fail, dependency violation = yes
global-lsn-prefix: pass
dep-frontier-lsn: pass
dep-frontier-cstamp: pass
```

これは、`U writes x; T reads x and writes y; U -> T` で、
`T` の log が `U` より先に durable になった場合を確認する。
local-only は `U` durable 前に `T` ack を返せるので unsafe。
global prefix と dep frontier は `T` ack を止める。

### abort0 main performance

```sh
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment abort0 \
  --seconds 5 \
  --repeats 5 \
  --threads 1,2,4,8,16,32 \
  --logger-num 4 \
  --group-size 8 \
  --flush-us 100
```

比較 mode:

```text
ermia_no_durability
ermia_pwal_group_global_prefix
ermia_async_global_lsn_prefix
ermia_async_dep_frontier_lsn
ermia_async_dep_frontier_cstamp
```

### dependency density

```sh
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment dependency \
  --seconds 5 \
  --repeats 5 \
  --thread-num 32 \
  --remote-ppm 0,10000,50000,100000,500000,1000000 \
  --logger-num 4
```

`--remote-ppm` は partitioned YCSB で read operation が remote partition を読む確率である。
write は local のままなので、CC conflict を抑えながら actual read dependency を増やす。

比較 mode:

```text
ermia_async_global_lsn_prefix
ermia_async_dep_frontier_lsn
ermia_async_dep_frontier_cstamp
```

### straggler

```sh
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment straggler \
  --seconds 5 \
  --repeats 5 \
  --thread-num 32 \
  --remote-ppm 0,10000,100000,1000000 \
  --logger-num 4 \
  --straggler-logger 3 \
  --straggler-sleep-us 1000
```

比較 mode:

```text
ermia_async_global_lsn_prefix
ermia_async_dep_frontier_cstamp
```

### cstamp vs separate LSN

```sh
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment cstamp \
  --seconds 5 \
  --repeats 5 \
  --threads 1,2,4,8,16,32 \
  --logger-num 4
```

この experiment は real I/O と I/O-light の両方を出す。

```text
real_io: fdatasync あり
io_light: CCBENCH_WAL_SKIP_FDATASYNC=1, group_size=64, flush_us=1000
```

ここで見る主指標は:

```text
global_atomic_per_tx
lsn_alloc_ns_per_tx
durable_ack_tps
ack_latency_p99_us
```
