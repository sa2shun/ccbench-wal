# ERMIA / P-WAL durability bottleneck report

日付: 2026-06-04

ブランチ: `wal-clean-abort0-framework`

この資料は、ERMIA + WAL / ERMIA + P-WAL の throughput がなぜその値で止まるのかを説明するための実験メモ。主結果は no-CC WAL durability microbench を 5回 rerun した値で、latency は sampled p50/p99 ではなく closed-loop average latency を主に見る。

## 1. 先に結論

今回の測定で見えている支配的なボトルネックは次の通り。

| 対象 | 主ボトルネック | 根拠 |
|---|---|---|
| ERMIA+WAL | shared WAL mutex | abort0 でも約 8.6K tx/s。off-CPU futex 約 95% |
| ERMIA+P-WAL | `fdatasync` + notify/global prefix wait | abort0 でも約 55K tx/s。off-CPU `fdatasync` 約 86% |
| no-CC single WAL per-txn fdatasync | global WAL mutex | 32 threads で 17.5K tx/s。mutex wait 96.75% |
| no-CC single WAL group commit | centralized fair baseline | 32 threads で 124K tx/s。naive single WAL より 7.1倍 |
| no-CC P-WAL per-txn fdatasync | per-txn sync | 32 threads で 150K tx/s。`fdatasync/s = tps` |
| no-CC P-WAL group commit | durable wait + storage sync | 32 threads で 156K tx/s。`fdatasync/s` は 20K まで減る |
| `pwal_group_commit_no_prefix` | local-durable only unsafe upper bound | 32 threads で 189K tx/s。ただし一般 workload では安全ではない |

読み替えると:

```text
1. single WAL per-txn fdatasync
   -> fdatasync を mutex 内で実行するので global serialization point になる

2. single WAL group commit
   -> naive single WAL より大幅に改善する
   -> 論文比較用の centralized WAL baseline として必須

3. P-WAL per-txn fdatasync
   -> shared WAL mutex は外れる
   -> ただし commit ごとに fdatasync する

4. P-WAL group commit
   -> fdatasync 回数を大きく減らす
   -> その後は durable point wait / prefix wait が見える

5. local-durable only upper bound
   -> 速いが一般には unsafe
   -> dependency-closed durable frontier の上限比較としてのみ使う
```

## 2. `no_prefix` の位置づけ

このレポート中の `pwal_group_commit_no_prefix` は、研究提案そのものではない。正確には:

```text
local-durable only unsafe upper bound
```

意味:

- worker が自分の logger shard の durable point だけを待つ。
- global durable prefix は待たない。
- transaction 間依存がない workload では妥当に見える。
- 一般の transaction workload では安全とは限らない。

安全でない例:

```text
logger 0: T0 commit
logger 1: T1 reads T0, then commits

dependency:
  T0 -> T1
```

このとき T1 の commit ack を返すには、T1 の log だけでなく T0 の log も durable である必要がある。`pwal_group_commit_no_prefix` は logger 1 の local durable point だけで T1 を返せてしまうので、一般には unsafe。

論文上の地図はこう置くのがよい。

| 方式 | 安全性 | 性能上の意味 |
|---|---|---|
| global durable prefix | safe | 保守的。遅い logger shard に全体が引っ張られる |
| local-durable only | unsafe in general | 依存を無視した上限値 |
| dependency-closed durable frontier | safe を狙う | local-only upper bound に近づける研究提案 |

## 3. 測定条件

主 no-CC microbench:

```text
results/no_cc_wal_microbench_20260604_225600/
```

CSV:

```text
results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600.csv
```

条件:

```text
seconds=2
repeats=5
thread_num=1,2,4,8,16,32
write_set_size=10
value_size=32
group_size=8
flush_us=100
I/O=buffered write + fdatasync
O_DIRECT=no
```

WAL file preallocation:

| mode | prealloc |
|---|---:|
| `single_wal` | 16 MiB per file |
| `single_wal_group_commit` | 256 MiB per file |
| `pwal_per_txn_fdatasync` | 16 MiB per worker file |
| `pwal_group_commit` | 64 MiB per logger file |
| `pwal_group_commit_no_prefix` | 64 MiB per logger file |

storage / filesystem:

| item | value |
|---|---|
| path | `/home/sa2shun/tx-playground/ccbench` |
| source | `/dev/mapper/vg0-lvhome[/sa2shun]` |
| filesystem | ext4 |
| mount options | `rw,nosuid,nodev,relatime,stripe=16` |
| tmpfs | no |
| block size | 4096 |

追加・更新したファイル:

- `tools/no_cc_wal_microbench.cc`
- `scripts/run_no_cc_wal_microbench.py`
- `scripts/plot_no_cc_wal_microbench_svg.py`

## 4. mode 定義

| mode | 位置づけ |
|---|---|
| `no_durability` | log record 生成だけ。durability なしの上限 |
| `single_wal` | naive lower baseline。shared WAL mutex 内で write + fdatasync |
| `single_wal_group_commit` | fair centralized WAL baseline。shared queue + single flusher |
| `pwal_per_txn_fdatasync` | WAL stream は分散するが、commit ごとに fdatasync |
| `pwal_group_commit` | P-WAL + group commit + global durable prefix |
| `pwal_group_commit_no_prefix` | local-durable only unsafe upper bound |

## 5. counter の読み方

| counter | 意味 |
|---|---|
| `throughput_tps` | `commits / actual_sec` |
| `closed_loop_avg_latency_us` | `thread_num * actual_sec * 1e6 / commits` |
| `latency_p50_us`, `latency_p99_us` | 256 commit ごとの sampled latency。参考値 |
| `fdatasync_per_sec` | `fdatasync_count / actual_sec` |
| `commits_per_fdatasync` | batching 効率 |
| `payload_build_ns` | log record construction |
| `mutex_wait_ns` | shared mutex / queue mutex を取るまでの時間 |
| `write_ns` | `write(2)` 時間 |
| `fdatasync_ns` | `fdatasync(2)` 時間 |
| `prefix_wait_ns` | durable point wait。no-prefix では local durable wait |
| `logger_min_local_seq`, `logger_max_local_seq` | logger shard の進行差を見る値 |

重要:

- closed-loop workload の平均 latency は `closed_loop_avg_latency_us` を見る。
- sampled p50/p99 は single WAL で明らかに平均 latency と整合しないため、主張の根拠にしない。
- `prefix_wait_ns` という名前だが、`pwal_group_commit_no_prefix` では global prefix wait ではなく local durable wait。

## 6. latency 計測の修正点

以前の表では single WAL の sampled p50/p99 が小さすぎた。

32 threads の 5回平均:

| mode | closed-loop avg | sampled p50 | sampled p99 | 判定 |
|---|---:|---:|---:|---|
| `no_durability` | 3.7 us | 3.0 us | 4.8 us | 整合 |
| `single_wal` | 1,831.1 us | 30.0 us | 132.4 us | 不整合。sample は信用しない |
| `single_wal_group_commit` | 257.9 us | 258.0 us | 309.2 us | 整合 |
| `pwal_per_txn_fdatasync` | 213.3 us | 155.4 us | 753.4 us | tail は見えるが平均は closed-loop を使う |
| `pwal_group_commit` | 205.6 us | 188.8 us | 359.6 us | 概ね整合 |
| `pwal_group_commit_no_prefix` | 169.3 us | 169.8 us | 239.2 us | 整合。ただし unsafe upper bound |

single WAL では Little's law の平均と sampled latency が一致しない。原因は、sample が per-worker seq の 256 commit ごとなので、mutex convoy / unfair lock acquisition の長い待ちを代表できていない可能性が高い。

したがって single WAL の latency は次で見る。

```text
closed_loop_avg_latency_us = 1,831 us
mutex wait per transaction = 1,772 us
fdatasync per transaction = 46 us
```

## 7. throughput

![throughput](../results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_throughput_tps.svg)

5回平均、単位 tx/s。

| mode | 1 | 2 | 4 | 8 | 16 | 32 | 32/1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `no_durability` | 343,491 | 662,220 | 1,315,396 | 2,483,295 | 4,764,786 | 8,676,499 | 25.26x |
| `single_wal` | 19,933 | 18,828 | 18,204 | 18,363 | 17,537 | 17,476 | 0.88x |
| `single_wal_group_commit` | 5,231 | 9,697 | 16,829 | 59,675 | 70,825 | 124,298 | 23.76x |
| `pwal_per_txn_fdatasync` | 19,937 | 35,704 | 64,529 | 112,395 | 139,797 | 150,013 | 7.52x |
| `pwal_group_commit` | 5,268 | 9,735 | 17,194 | 63,413 | 83,892 | 155,840 | 29.58x |
| `pwal_group_commit_no_prefix` | 5,259 | 9,720 | 17,183 | 64,987 | 107,504 | 189,002 | 35.94x |

32-thread min/max:

| mode | mean | stddev | min | max |
|---|---:|---:|---:|---:|
| `no_durability` | 8,676,499 | 661,902 | 7,896,347 | 9,228,681 |
| `single_wal` | 17,476 | 86 | 17,418 | 17,625 |
| `single_wal_group_commit` | 124,298 | 5,805 | 120,145 | 133,647 |
| `pwal_per_txn_fdatasync` | 150,013 | 261 | 149,772 | 150,395 |
| `pwal_group_commit` | 155,840 | 6,680 | 150,750 | 167,336 |
| `pwal_group_commit_no_prefix` | 189,002 | 1,128 | 187,560 | 190,275 |

読み方:

- naive `single_wal` は 32 threads にしても伸びない。shared WAL mutex が直列化している。
- `single_wal_group_commit` は 124K tx/s まで伸びる。これは fair centralized WAL baseline として必要。
- `pwal_group_commit` は `single_wal_group_commit` より 1.25倍高い。
- `pwal_group_commit_no_prefix` はさらに高いが、local-durable only unsafe upper bound。

## 8. closed-loop average latency

![closed-loop average latency](../results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_closed_loop_avg_latency_us.svg)

32 threads、5回平均。

| mode | closed-loop avg latency | sampled p99 |
|---|---:|---:|
| `no_durability` | 3.7 us | 4.8 us |
| `single_wal` | 1,831.1 us | 132.4 us |
| `single_wal_group_commit` | 257.9 us | 309.2 us |
| `pwal_per_txn_fdatasync` | 213.3 us | 753.4 us |
| `pwal_group_commit` | 205.6 us | 359.6 us |
| `pwal_group_commit_no_prefix` | 169.3 us | 239.2 us |

single WAL の sampled p99 は明らかに過小。平均 latency と counter の方が正しい。

## 9. fdatasync frequency

![fdatasync per sec](../results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_fdatasync_per_sec.svg)

![commits per fdatasync](../results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_commits_per_fdatasync.svg)

32 threads、5回平均。

| mode | throughput | fdatasync/s | commits/fdatasync | 解釈 |
|---|---:|---:|---:|---|
| `single_wal` | 17,476 | 17,476 | 1.00 | 1 commit 1 sync、かつ mutex 内 |
| `single_wal_group_commit` | 124,298 | 7,757 | 16.02 | centralized group commit |
| `pwal_per_txn_fdatasync` | 150,013 | 150,013 | 1.00 | P-WAL でも per-txn sync は残る |
| `pwal_group_commit` | 155,840 | 20,022 | 7.78 | P-WAL group commit |
| `pwal_group_commit_no_prefix` | 189,002 | 23,627 | 8.00 | unsafe upper bound |

重要な関係:

```text
throughput ~= fdatasync/s * commits_per_fdatasync
```

例:

```text
pwal_group_commit:
  20,022 fdatasync/s * 7.78 commits/fdatasync ~= 155K tx/s

single_wal_group_commit:
  7,757 fdatasync/s * 16.02 commits/fdatasync ~= 124K tx/s
```

## 10. 32-thread counter 詳細

5回平均。

| mode | tps | closed avg us | MB/s | fdatasync/s | commits/fdatasync | build% | mutex% | write% | fdatasync% | durable wait% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `no_durability` | 8,676,499 | 3.7 | 5,349.3 | 0 | 0.00 | 83.15 | 0.00 | 0.00 | 0.00 | 0.00 |
| `single_wal` | 17,476 | 1,831.1 | 10.6 | 17,476 | 1.00 | 0.21 | 96.75 | 0.28 | 2.53 | 0.00 |
| `single_wal_group_commit` | 124,298 | 257.9 | 76.1 | 7,757 | 16.02 | 1.38 | 4.33 | 0.28 | 2.81 | 91.87 |
| `pwal_per_txn_fdatasync` | 150,013 | 213.3 | 91.8 | 150,013 | 1.00 | 1.76 | 0.00 | 3.16 | 94.79 | 0.00 |
| `pwal_group_commit` | 155,840 | 205.6 | 95.5 | 20,022 | 7.78 | 1.71 | 2.32 | 0.56 | 8.14 | 93.43 |
| `pwal_group_commit_no_prefix` | 189,002 | 169.3 | 115.8 | 23,627 | 8.00 | 1.96 | 3.90 | 0.69 | 10.25 | 91.54 |

transaction あたり平均:

| mode | build | mutex wait | write | fdatasync | durable wait |
|---|---:|---:|---:|---:|---:|
| `no_durability` | 3.1 us | 0.0 us | 0.0 us | 0.0 us | 0.0 us |
| `single_wal` | 3.9 us | 1,771.5 us | 5.1 us | 46.4 us | 0.0 us |
| `single_wal_group_commit` | 3.6 us | 11.2 us | 0.7 us | 7.3 us | 236.9 us |
| `pwal_per_txn_fdatasync` | 3.8 us | 0.0 us | 6.7 us | 202.2 us | 0.0 us |
| `pwal_group_commit` | 3.5 us | 4.8 us | 1.2 us | 16.7 us | 192.1 us |
| `pwal_group_commit_no_prefix` | 3.3 us | 6.6 us | 1.2 us | 17.4 us | 155.0 us |

読み方:

- `single_wal` は `fdatasync` 自体より mutex wait が圧倒的に大きい。
- `single_wal_group_commit` は mutex wait と per-txn fdatasync cost を大きく減らすが、single flusher の durable wait が残る。
- `pwal_per_txn_fdatasync` は shared mutex はないが、per-txn `fdatasync` が 202 us/tx 残る。
- `pwal_group_commit` は `fdatasync` cost を 16.7 us/tx まで減らす。
- `pwal_group_commit_no_prefix` は一番速いが、local-durable only unsafe upper bound。

## 11. perf stat

代表 32-thread run。各 1秒。perf stat は補助証拠であり、throughput の主表は 5回 rerun を使う。

| mode | CPU 利用 | context switches/sec | IPC | 読み方 |
|---|---:|---:|---:|---|
| `single_wal` | 0.650 CPUs | 76.8K | 1.35 | 多くの worker が mutex/futex で寝る |
| `single_wal_group_commit` | 31.433 CPUs | 3.1K | 1.99 | group commit で worker が進める |
| `pwal_per_txn_fdatasync` | 4.924 CPUs | 45.5K | 0.87 | sync I/O 待ちが多い |
| `pwal_group_commit` | 32.322 CPUs | 4.7K | 2.00 | batching 後は CPU を使う |
| `pwal_group_commit_no_prefix` | 32.384 CPUs | 6.0K | 1.98 | unsafe upper bound。local wait のみ |

perf stat artifacts:

```text
results/no_cc_wal_perf_20260604_225600/
```

## 12. ERMIA 実 workload との対応

既存の ERMIA TX breakdown 32 threads。

| workload | protocol | tps | abort_rate | ERMIA other | SSN | WAL | WAL 内の主成分 |
|---|---|---:|---:|---:|---:|---:|---|
| normal_ycsb | ERMIA+WAL | 8,512 | 0.0162 | 0.83% | 0.92% | 123.17% | mutex wait 119.44% |
| normal_ycsb | ERMIA+P-WAL | 54,548 | 0.0155 | 3.37% | 1.18% | 118.62% | fdatasync 77.96%, notify wait 14.89% |
| abort0_ycsb | ERMIA+WAL | 8,583 | 0.0000 | 0.18% | 0.01% | 124.77% | mutex wait 121.04% |
| abort0_ycsb | ERMIA+P-WAL | 55,262 | 0.0000 | 1.56% | 0.30% | 121.27% | fdatasync 79.99%, notify wait 15.22% |

abort0 でも throughput がほぼ変わらない。

| protocol | normal_ycsb | abort0_ycsb | 読み方 |
|---|---:|---:|---|
| ERMIA+WAL | 8,512 tx/s | 8,583 tx/s | abort を消しても上がらない。shared WAL 側が壁 |
| ERMIA+P-WAL | 54,548 tx/s | 55,262 tx/s | abort を消しても上がらない。durability 側が壁 |

したがって、この範囲の主因は CC conflict ではなく durability protocol。

## 13. ERMIA perf / off-CPU

off-CPU Flame Graph の要約。

| workload | protocol | total off-CPU | futex | fdatasync | write | 読み方 |
|---|---|---:|---:|---:|---:|---|
| normal_ycsb | ERMIA+WAL | 96.299 s | 95.05% | 1.83% | 0.00% | shared WAL mutex で寝ている |
| normal_ycsb | ERMIA+P-WAL | 52.965 s | 3.16% | 86.55% | 4.62% | mutex は外れ、fdatasync が支配 |
| abort0_ycsb | ERMIA+WAL | 97.752 s | 95.03% | 1.90% | 0.00% | abort0 でも shared WAL mutex |
| abort0_ycsb | ERMIA+P-WAL | 55.145 s | 4.23% | 85.64% | 4.69% | abort0 でも fdatasync |

Flame Graph:

- [normal_wal_32_offcpu.svg](../results/offcpu_flamegraph_20260604_1425/normal_wal_32_offcpu.svg)
- [normal_pwal_32_offcpu.svg](../results/offcpu_flamegraph_20260604_1425/normal_pwal_32_offcpu.svg)
- [abort0_wal_32_offcpu.svg](../results/offcpu_flamegraph_20260604_1425/abort0_wal_32_offcpu.svg)
- [abort0_pwal_32_offcpu.svg](../results/offcpu_flamegraph_20260604_1425/abort0_pwal_32_offcpu.svg)

CPU Flame Graph では、shared WAL は `TxExecutor::ssn_parallel_commit` 配下、P-WAL は `ccbench::WalLogger::logPerThread` 配下に sample が寄る。wait の実体は off-CPU のほうが分かりやすい。

## 14. straggler で見る global prefix

P-WAL の logger は並列でも、commit return 条件が global durable prefix のままだと、遅い logger shard に全体が引っ張られる。

条件:

```text
thread_num=32
logger_num=4
group_size=8
flush_us=100
prealloc_mb=64
straggler_logger=3
straggler_sleep_us=1000
```

結果:

| mode | straggler | throughput | closed avg | p99 sample | fdatasync/s | commits/fdatasync | logger local seq min/max | 読み方 |
|---|---|---:|---:|---:|---:|---:|---|---|
| `pwal_group_commit` | no | 155,840 | 205.6 us | 359.6 us | 20,022 | 7.78 | 77,948 / 77,948 | global prefix、安全 |
| `pwal_group_commit_no_prefix` | no | 189,002 | 169.3 us | 239.2 us | 23,627 | 8.00 | 94,193 / 94,996 | unsafe upper bound |
| `pwal_group_commit` | logger 3 + 1ms | 25,771 | 1,241.7 us | 1,288 us | 4,844 | 5.32 | 12,890 / 12,890 | 全 logger が straggler に揃えられる |
| `pwal_group_commit_no_prefix` | logger 3 + 1ms | 144,390 | 221.6 us | 1,288 us | 18,051 | 8.00 | 12,832 / 92,881 | 速い logger は進める。ただし unsafe |

global prefix 版では、`logger local seq min/max` が `12,890 / 12,890` になっている。速い logger も遅い logger と同じ位置で止まる。

local-durable only upper bound では、`12,832 / 92,881` まで差が開く。速い logger は進める。ただし依存を見ていないので一般には安全でない。

この差が、dependency-closed durable frontier を研究する動機になる。

## 15. 何がどの bottleneck か

### single WAL per-txn fdatasync

```text
throughput: 17,476 tx/s
closed-loop avg latency: 1,831 us
mutex wait: 96.75%
off-CPU ERMIA WAL futex: about 95%
```

ボトルネックは storage bandwidth ではなく shared WAL mutex。`fdatasync` を mutex 内で実行しているため、1 worker の durable sync latency が全 worker の待ち列になる。

### single WAL group commit

```text
throughput: 124,298 tx/s
commits/fdatasync: 16.02
closed-loop avg latency: 258 us
```

naive single WAL よりはるかに強い baseline。論文比較では必須。これを入れることで、P-WAL の優位性を「single WAL 実装が雑なだけ」と言われにくくなる。

### P-WAL per-txn fdatasync

```text
throughput: 150,013 tx/s
fdatasync/s: 150,013
commits/fdatasync: 1.00
fdatasync: 94.79%
```

shared WAL mutex は外れるが、1 commit 1 fdatasync がそのまま残る。preallocation 後は throughput が高く出ているが、`fdatasync/s = throughput` なので per-txn sync 設計であることは変わらない。

### P-WAL group commit

```text
throughput: 155,840 tx/s
fdatasync/s: 20,022
commits/fdatasync: 7.78
closed-loop avg latency: 206 us
```

fdatasync 回数は大きく減る。throughput は per-txn P-WAL から大きくは伸びていないが、tail latency と sync frequency は改善している。ここから先は durable point wait / prefix wait が主に見える。

### local-durable only unsafe upper bound

```text
throughput: 189,002 tx/s
closed-loop avg latency: 169 us
```

速いが safe protocol ではない。依存がない workload でのみ上限値として解釈する。

## 16. 最終整理

今回の結果は次の主張を支える。

```text
高並行 CC に素朴な WAL durability を足すと、
CC conflict ではなく durability protocol が bottleneck になる。

single WAL per-txn fdatasync は shared mutex で直列化される。

single WAL group commit は必須 baseline であり、
naive single WAL より大幅に速い。

P-WAL は shared mutex を外せるが、
per-txn fdatasync のままでは sync cost が残る。

P-WAL group commit で sync frequency は下がる。
その後に durable point wait / global prefix が見える。

local-only durable wait は速いが unsafe upper bound。
研究の狙いは、safe な dependency-closed durable frontier で
global prefix より local-only upper bound に近づけること。
```
