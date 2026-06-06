# WAL / P-WAL Bottleneck Report

日付: 2026-06-04

対象ブランチ: `wal-clean-abort0-framework`

対象リポジトリ: `tx-playground/ccbench`

## Abstract

本レポートの結論は、ERMIA に WAL / P-WAL durability を追加したときの主ボトルネックは、CC conflict ではなく durability protocol 側にある、ということである。ERMIA+WAL は shared WAL mutex により 32 threads でも約 8.6K tx/s に止まり、off-CPU Flame Graph では futex wait が約 95% を占める。ERMIA+P-WAL は shared mutex をほぼ解消するが、次に `fdatasync` と notify/global prefix wait が支配的になり、abort0 workload でも約 55K tx/s に止まる。

no-CC WAL durability microbench では、ERMIA / Masstree / validation / abort / retry を除去して durability だけを測った。その結果、naive single WAL は 32 threads で 17.5K tx/s、single WAL group commit は 124K tx/s、P-WAL per-transaction fdatasync は 150K tx/s、P-WAL group commit は 156K tx/s、local-durable only upper bound は 189K tx/s だった。したがって、単に P-WAL と single WAL を比較するだけではなく、single WAL group commit を fair centralized WAL baseline として入れる必要がある。

`pwal_group_commit_no_prefix` は高速だが、一般の transaction workload では unsafe である。これは dependency を見ずに local logger の durable point だけを待つためである。研究上の狙いは、global durable prefix より保守性を減らしつつ、local-only upper bound に近づく safe な dependency-closed durable frontier を作ることにある。

## Table 1. Bottleneck Summary

| 対象 | 32 threads throughput | 主ボトルネック | 主要根拠 |
|---|---:|---|---|
| ERMIA+WAL normal_ycsb | 8,512 tx/s | shared WAL mutex | WAL 123.17%, WAL mutex wait 119.44%, off-CPU futex 95.05% |
| ERMIA+WAL abort0_ycsb | 8,583 tx/s | shared WAL mutex | abort 0.0000, WAL 124.77%, WAL mutex wait 121.04%, off-CPU futex 95.03% |
| ERMIA+P-WAL normal_ycsb | 54,548 tx/s | `fdatasync` + notify wait | WAL 118.62%, fdatasync 77.96%, notify wait 14.89%, off-CPU fdatasync 86.55% |
| ERMIA+P-WAL abort0_ycsb | 55,262 tx/s | `fdatasync` + notify wait | abort 0.0000, WAL 121.27%, fdatasync 79.99%, notify wait 15.22%, off-CPU fdatasync 85.64% |
| no-CC single WAL | 17,476 tx/s | global WAL mutex | mutex wait 96.75%, closed-loop avg 1,831 us |
| no-CC single WAL group commit | 124,298 tx/s | single flusher durable wait | commits/fdatasync 16.02, durable wait 91.87% |
| no-CC P-WAL per-txn fdatasync | 150,013 tx/s | per-transaction `fdatasync` | fdatasync/s = 150,013, fdatasync 94.79% |
| no-CC P-WAL group commit | 155,840 tx/s | durable point wait + storage sync | fdatasync/s 20,022, commits/fdatasync 7.78, durable wait 93.43% |
| no-CC local-only upper bound | 189,002 tx/s | unsafe upper bound | local durable wait only, generally unsafe |

## 1. Experimental Setup

### 1.1 ERMIA workload

ERMIA 側は以下を測定した。

| item | value |
|---|---|
| workload | `normal_ycsb`, `abort0_ycsb` |
| protocol | `ermia_wal`, `ermia_pwal` |
| threads | 1, 2, 4, 8, 16, 32 |
| reported table | 32-thread result |
| duration | benchmark option `seconds=5`; measured `actual_extime=4.0` |
| source artifact | `results/tx_breakdown_20260604_135550.txt` |

`abort0_ycsb` は key partition により abort をほぼ 0 にした workload である。目的は CC conflict と durability bottleneck を分離することである。

### 1.2 no-CC WAL durability microbench

no-CC microbench は、ERMIA / Masstree / validation / abort / retry を外し、以下だけを測る。

```text
pseudo write set
  -> log record construction
  -> WAL append
  -> fdatasync / durable point wait
  -> commit complete
```

主実験条件:

| item | value |
|---|---|
| source artifact | `results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600.csv` |
| repeats | 5 |
| duration per run | 2 sec |
| threads | 1, 2, 4, 8, 16, 32 |
| write set size | 10 |
| value size | 32 bytes |
| group size | 8 |
| flush interval | 100 us |
| I/O | buffered `write(2)` + `fdatasync(2)` |
| O_DIRECT | no |

WAL file preallocation:

| mode | preallocation |
|---|---:|
| `single_wal` | 16 MiB |
| `single_wal_group_commit` | 256 MiB |
| `pwal_per_txn_fdatasync` | 16 MiB per worker file |
| `pwal_group_commit` | 64 MiB per logger file |
| `pwal_group_commit_no_prefix` | 64 MiB per logger file |

Storage / filesystem:

| item | value |
|---|---|
| path | `/home/sa2shun/tx-playground/ccbench` |
| device source | `/dev/mapper/vg0-lvhome[/sa2shun]` |
| filesystem | ext4 |
| mount options | `rw,nosuid,nodev,relatime,stripe=16` |
| tmpfs | no |
| block size | 4096 |

## 2. Protocol Definitions

| mode | definition | safety / baseline role |
|---|---|---|
| `no_durability` | log record construction only | upper bound without durability |
| `single_wal` | one shared WAL file, global mutex, write + fdatasync per transaction | naive lower baseline |
| `single_wal_group_commit` | shared log buffer, one flusher, batched write + fdatasync | fair centralized WAL baseline |
| `pwal_per_txn_fdatasync` | per-worker WAL file, fdatasync per transaction | P-WAL without sync batching |
| `pwal_group_commit` | logger shards, batched fdatasync, global durable prefix wait | safe P-WAL group commit model |
| `pwal_group_commit_no_prefix` | logger shards, batched fdatasync, local durable point only | local-durable only unsafe upper bound |

`pwal_group_commit_no_prefix` は研究提案ではない。一般の transaction workload では unsafe である。

unsafe になる例:

```text
logger 0: T0 commits
logger 1: T1 reads T0 and then commits

dependency:
  T0 -> T1
```

T1 の commit ack を返すには T1 の log だけでなく T0 の log も durable である必要がある。local durable point だけを見ると、この依存を無視して T1 を返せるため、一般には safe ではない。

## 3. ERMIA Workload Results

### 3.1 32-thread ERMIA breakdown

| workload | protocol | throughput | abort rate | ERMIA other | SSN | WAL | WAL mutex | WAL write | WAL fdatasync | WAL notify |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| normal_ycsb | ERMIA+WAL | 8,512 | 0.0162 | 0.83% | 0.92% | 123.17% | 119.44% | 0.35% | 3.10% | 0.00% |
| normal_ycsb | ERMIA+P-WAL | 54,548 | 0.0155 | 3.37% | 1.18% | 118.62% | 0.04% | 4.52% | 77.96% | 14.89% |
| abort0_ycsb | ERMIA+WAL | 8,583 | 0.0000 | 0.18% | 0.01% | 124.77% | 121.04% | 0.35% | 3.11% | 0.00% |
| abort0_ycsb | ERMIA+P-WAL | 55,262 | 0.0000 | 1.56% | 0.30% | 121.27% | 0.04% | 4.63% | 79.99% | 15.22% |

Percentages can exceed 100% because the timer accumulates per-thread wait time over wall-clock time. Use the table as phase attribution, not as exclusive CPU utilization.

### 3.2 Abort0 comparison

| protocol | normal_ycsb | abort0_ycsb | abort0 / normal | interpretation |
|---|---:|---:|---:|---|
| ERMIA+WAL | 8,512 | 8,583 | 1.01x | abort を消しても throughput はほぼ変わらない |
| ERMIA+P-WAL | 54,548 | 55,262 | 1.01x | abort を消しても throughput はほぼ変わらない |

この表が示すことは明確である。abort0 workload でも throughput が上がらないため、今回の 32-thread 結果の主因は CC conflict ではない。durability protocol 側が支配的である。

### 3.3 P-WAL speedup over WAL

| workload | ERMIA+WAL | ERMIA+P-WAL | speedup |
|---|---:|---:|---:|
| normal_ycsb | 8,512 | 54,548 | 6.41x |
| abort0_ycsb | 8,583 | 55,262 | 6.44x |

P-WAL により shared WAL mutex は大きく改善する。しかし P-WAL でも throughput は約 55K tx/s に止まる。これは P-WAL 後の bottleneck が `fdatasync` と notify/global prefix wait に移ったためである。

## 4. Perf / Off-CPU Evidence

off-CPU Flame Graph の要約:

| workload | protocol | total off-CPU | futex | fdatasync | write | interpretation |
|---|---|---:|---:|---:|---:|---|
| normal_ycsb | ERMIA+WAL | 96.299 s | 95.05% | 1.83% | 0.00% | shared WAL mutex wait |
| normal_ycsb | ERMIA+P-WAL | 52.965 s | 3.16% | 86.55% | 4.62% | fdatasync/writeback wait |
| abort0_ycsb | ERMIA+WAL | 97.752 s | 95.03% | 1.90% | 0.00% | shared WAL mutex wait |
| abort0_ycsb | ERMIA+P-WAL | 55.145 s | 4.23% | 85.64% | 4.69% | fdatasync/writeback wait |

Flame Graph artifacts:

- [normal_wal_32_offcpu.svg](../results/offcpu_flamegraph_20260604_1425/normal_wal_32_offcpu.svg)
- [normal_pwal_32_offcpu.svg](../results/offcpu_flamegraph_20260604_1425/normal_pwal_32_offcpu.svg)
- [abort0_wal_32_offcpu.svg](../results/offcpu_flamegraph_20260604_1425/abort0_wal_32_offcpu.svg)
- [abort0_pwal_32_offcpu.svg](../results/offcpu_flamegraph_20260604_1425/abort0_pwal_32_offcpu.svg)

CPU Flame Graph では、ERMIA+WAL は `TxExecutor::ssn_parallel_commit` 配下に sample が寄り、ERMIA+P-WAL は `ccbench::WalLogger::logPerThread` 配下に sample が寄る。ただし、この workload の本質は wait time なので、bottleneck の判定には off-CPU Flame Graph の方が直接的である。

## 5. no-CC WAL Durability Microbench

### 5.1 Throughput scaling

![throughput](../results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_throughput_tps.svg)

5回平均、単位は tx/s。

| mode | 1 | 2 | 4 | 8 | 16 | 32 | 32/1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `no_durability` | 343,491 | 662,220 | 1,315,396 | 2,483,295 | 4,764,786 | 8,676,499 | 25.26x |
| `single_wal` | 19,933 | 18,828 | 18,204 | 18,363 | 17,537 | 17,476 | 0.88x |
| `single_wal_group_commit` | 5,231 | 9,697 | 16,829 | 59,675 | 70,825 | 124,298 | 23.76x |
| `pwal_per_txn_fdatasync` | 19,937 | 35,704 | 64,529 | 112,395 | 139,797 | 150,013 | 7.52x |
| `pwal_group_commit` | 5,268 | 9,735 | 17,194 | 63,413 | 83,892 | 155,840 | 29.58x |
| `pwal_group_commit_no_prefix` | 5,259 | 9,720 | 17,183 | 64,987 | 107,504 | 189,002 | 35.94x |

### 5.2 32-thread stability

5回 rerun の 32-thread summary。

| mode | mean tx/s | stddev | min | max |
|---|---:|---:|---:|---:|
| `no_durability` | 8,676,499 | 661,902 | 7,896,347 | 9,228,681 |
| `single_wal` | 17,476 | 86 | 17,418 | 17,625 |
| `single_wal_group_commit` | 124,298 | 5,805 | 120,145 | 133,647 |
| `pwal_per_txn_fdatasync` | 150,013 | 261 | 149,772 | 150,395 |
| `pwal_group_commit` | 155,840 | 6,680 | 150,750 | 167,336 |
| `pwal_group_commit_no_prefix` | 189,002 | 1,128 | 187,560 | 190,275 |

### 5.3 Speedup table

| comparison | speedup |
|---|---:|
| `single_wal_group_commit` / `single_wal` | 7.11x |
| `pwal_per_txn_fdatasync` / `single_wal` | 8.58x |
| `pwal_group_commit` / `single_wal` | 8.92x |
| `pwal_group_commit_no_prefix` / `single_wal` | 10.81x |
| `pwal_per_txn_fdatasync` / `single_wal_group_commit` | 1.21x |
| `pwal_group_commit` / `single_wal_group_commit` | 1.25x |
| `pwal_group_commit_no_prefix` / `single_wal_group_commit` | 1.52x |
| `pwal_group_commit` / `pwal_per_txn_fdatasync` | 1.04x |
| `pwal_group_commit_no_prefix` / `pwal_per_txn_fdatasync` | 1.26x |

この表で重要なのは、`single_wal_group_commit` が naive `single_wal` より 7.11倍速い点である。したがって、論文上は naive single WAL だけでは不十分であり、centralized group commit baseline が必要である。

## 6. Latency Validity

closed-loop workload の平均 latency は Little's law から求める。

```text
closed_loop_avg_latency_us = thread_num * actual_sec * 1e6 / commits
```

![closed-loop average latency](../results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_closed_loop_avg_latency_us.svg)

32-thread result:

| mode | closed-loop avg latency | sampled p50 | sampled p99 | interpretation |
|---|---:|---:|---:|---|
| `no_durability` | 3.7 us | 3.0 us | 4.8 us | consistent |
| `single_wal` | 1,831.1 us | 30.0 us | 132.4 us | sampled latency is invalid for this mode |
| `single_wal_group_commit` | 257.9 us | 258.0 us | 309.2 us | consistent |
| `pwal_per_txn_fdatasync` | 213.3 us | 155.4 us | 753.4 us | average from closed-loop, tail from sample |
| `pwal_group_commit` | 205.6 us | 188.8 us | 359.6 us | mostly consistent |
| `pwal_group_commit_no_prefix` | 169.3 us | 169.8 us | 239.2 us | consistent but unsafe upper bound |

`single_wal` の sampled p50/p99 は信用してはいけない。32 threads で 17,476 tx/s なら、closed-loop average は約 1.83 ms になる。counter でも transaction あたり mutex wait は 1.77 ms であり、sampled p99 132 us とは整合しない。

## 7. fdatasync and Batching

![fdatasync per sec](../results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_fdatasync_per_sec.svg)

![commits per fdatasync](../results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_commits_per_fdatasync.svg)

32-thread result:

| mode | throughput | fdatasync/s | commits/fdatasync | throughput relation |
|---|---:|---:|---:|---|
| `single_wal` | 17,476 | 17,476 | 1.00 | 1 tx per sync |
| `single_wal_group_commit` | 124,298 | 7,757 | 16.02 | 7,757 * 16.02 = 124K |
| `pwal_per_txn_fdatasync` | 150,013 | 150,013 | 1.00 | 1 tx per sync |
| `pwal_group_commit` | 155,840 | 20,022 | 7.78 | 20,022 * 7.78 = 156K |
| `pwal_group_commit_no_prefix` | 189,002 | 23,627 | 8.00 | 23,627 * 8.00 = 189K |

この表から、group commit の効果は `fdatasync/s` と `commits/fdatasync` の積で説明できる。P-WAL group commit は `fdatasync/s` を per-txn P-WAL の 150K/s から 20K/s まで下げている。一方で throughput が 150K から 156K へしか伸びていないのは、durable point wait が残るためである。

## 8. Counter Breakdown at 32 Threads

### 8.1 Accounted percentage

| mode | throughput | closed avg | build | mutex | write | fdatasync | durable wait |
|---|---:|---:|---:|---:|---:|---:|---:|
| `no_durability` | 8,676,499 | 3.7 us | 83.15% | 0.00% | 0.00% | 0.00% | 0.00% |
| `single_wal` | 17,476 | 1,831.1 us | 0.21% | 96.75% | 0.28% | 2.53% | 0.00% |
| `single_wal_group_commit` | 124,298 | 257.9 us | 1.38% | 4.33% | 0.28% | 2.81% | 91.87% |
| `pwal_per_txn_fdatasync` | 150,013 | 213.3 us | 1.76% | 0.00% | 3.16% | 94.79% | 0.00% |
| `pwal_group_commit` | 155,840 | 205.6 us | 1.71% | 2.32% | 0.56% | 8.14% | 93.43% |
| `pwal_group_commit_no_prefix` | 189,002 | 169.3 us | 1.96% | 3.90% | 0.69% | 10.25% | 91.54% |

### 8.2 Per-transaction counter

| mode | build | mutex wait | write | fdatasync | durable wait |
|---|---:|---:|---:|---:|---:|
| `no_durability` | 3.1 us | 0.0 us | 0.0 us | 0.0 us | 0.0 us |
| `single_wal` | 3.9 us | 1,771.5 us | 5.1 us | 46.4 us | 0.0 us |
| `single_wal_group_commit` | 3.6 us | 11.2 us | 0.7 us | 7.3 us | 236.9 us |
| `pwal_per_txn_fdatasync` | 3.8 us | 0.0 us | 6.7 us | 202.2 us | 0.0 us |
| `pwal_group_commit` | 3.5 us | 4.8 us | 1.2 us | 16.7 us | 192.1 us |
| `pwal_group_commit_no_prefix` | 3.3 us | 6.6 us | 1.2 us | 17.4 us | 155.0 us |

Interpretation:

- `single_wal` は `fdatasync` 自体より mutex wait が大きい。`fdatasync` を mutex 内で実行するため、global lock wait が 1.77 ms/tx まで膨らむ。
- `single_wal_group_commit` は mutex wait と fdatasync cost を大きく下げるが、worker は durable point を待つため durable wait が 236.9 us/tx 残る。
- `pwal_per_txn_fdatasync` は mutex wait を消すが、fdatasync が 202.2 us/tx 残る。
- `pwal_group_commit` は fdatasync を 16.7 us/tx まで下げるが、durable wait が 192.1 us/tx 残る。
- `pwal_group_commit_no_prefix` は一番速いが、safe protocol ではない。

## 9. perf stat Evidence

代表 32-thread run。perf stat は補助証拠であり、throughput の主表は 5回 rerun を使う。

| mode | CPU utilized | context switches/sec | IPC | interpretation |
|---|---:|---:|---:|---|
| `single_wal` | 0.650 CPUs | 76.8K | 1.35 | worker の大半が mutex/futex で寝る |
| `single_wal_group_commit` | 31.433 CPUs | 3.1K | 1.99 | group commit で worker が進める |
| `pwal_per_txn_fdatasync` | 4.924 CPUs | 45.5K | 0.87 | sync I/O wait が多い |
| `pwal_group_commit` | 32.322 CPUs | 4.7K | 2.00 | batching 後は CPU を使う |
| `pwal_group_commit_no_prefix` | 32.384 CPUs | 6.0K | 1.98 | local durable wait only; unsafe upper bound |

Artifacts:

```text
results/no_cc_wal_perf_20260604_225600/
```

## 10. Global Prefix Bottleneck

P-WAL では logger shard を並列化できる。しかし commit return condition が global durable prefix のままだと、遅い logger shard に全体が引っ張られる。

Straggler experiment:

| item | value |
|---|---|
| threads | 32 |
| logger_num | 4 |
| group_size | 8 |
| flush_us | 100 |
| prealloc_mb | 64 |
| straggler | logger 3 |
| injected delay | 1 ms before flush |

Result:

| mode | straggler | throughput | closed avg | sampled p99 | fdatasync/s | commits/fdatasync | logger local seq min/max | interpretation |
|---|---|---:|---:|---:|---:|---:|---|---|
| `pwal_group_commit` | no | 155,840 | 205.6 us | 359.6 us | 20,022 | 7.78 | 77,948 / 77,948 | global prefix, safe |
| `pwal_group_commit_no_prefix` | no | 189,002 | 169.3 us | 239.2 us | 23,627 | 8.00 | 94,193 / 94,996 | local-only upper bound, unsafe |
| `pwal_group_commit` | logger 3 + 1 ms | 25,771 | 1,241.7 us | 1,288 us | 4,844 | 5.32 | 12,890 / 12,890 | all loggers are pulled down to straggler |
| `pwal_group_commit_no_prefix` | logger 3 + 1 ms | 144,390 | 221.6 us | 1,288 us | 18,051 | 8.00 | 12,832 / 92,881 | fast loggers continue, but unsafe |

Global prefix 版では logger local seq が `12,890 / 12,890` になっている。つまり、速い logger も遅い logger と同じ位置で止まる。一方 local-only upper bound では `12,832 / 92,881` まで差が開き、速い logger は進める。ただしこれは依存関係を無視しているため unsafe である。

## 11. Bottleneck Narrative

### 11.1 Why ERMIA+WAL is slow

ERMIA+WAL は 32 threads で 8.5K tx/s 程度に止まる。abort0 にしても 8.6K tx/s なので、CC conflict は主因ではない。TX breakdown では WAL が 124.77%、WAL mutex wait が 121.04% を占める。off-CPU でも futex が約 95% を占める。したがって、主因は shared WAL mutex である。

### 11.2 What P-WAL improves

ERMIA+P-WAL は 32 threads で約 55K tx/s になり、ERMIA+WAL より約 6.4倍速い。WAL mutex wait は約 0.04% まで下がる。したがって、P-WAL は single WAL の shared mutex bottleneck を外せている。

### 11.3 What remains after P-WAL

P-WAL 後は `fdatasync` と notify/global prefix wait が残る。ERMIA+P-WAL abort0 では fdatasync 79.99%、notify wait 15.22% である。off-CPU でも fdatasync が 85.64% を占める。つまり、P-WAL によって mutex bottleneck は消えるが、durability sync と commit return ordering が次の bottleneck になる。

### 11.4 Why single WAL group commit matters

naive single WAL は 17.5K tx/s だが、single WAL group commit は 124K tx/s である。これは 7.11倍の差であり、naive single WAL だけを baseline にすると、P-WAL の効果を過大評価する危険がある。したがって paper baseline として single WAL group commit は必須である。

### 11.5 Why local-only is not the proposal

`pwal_group_commit_no_prefix` は 189K tx/s で最も速い。しかしこれは dependency を無視する local-durable only upper bound である。一般 workload では T0 -> T1 の依存があり得るため、T1 を返す前に T0 の log durability も保証しなければならない。研究提案は local-only ではなく、safe な dependency-closed durable frontier である。

## 12. Report-Ready Claims

報告書にそのまま使える主張は以下。

1. ERMIA+WAL の 32-thread throughput は normal_ycsb で 8,512 tx/s、abort0_ycsb で 8,583 tx/s であり、abort を除去しても改善しない。
2. ERMIA+WAL の off-CPU time は futex が約 95% を占め、shared WAL mutex が支配的である。
3. ERMIA+P-WAL は ERMIA+WAL より約 6.4倍高速化するが、abort0_ycsb でも 55,262 tx/s に止まる。
4. ERMIA+P-WAL の off-CPU time は `fdatasync` が約 86% を占めるため、P-WAL 後の bottleneck は storage durability である。
5. no-CC microbench では naive single WAL は 17,476 tx/s であり、single WAL group commit は 124,298 tx/s である。したがって fair baseline として single WAL group commit が必要である。
6. P-WAL per-txn fdatasync は 150,013 tx/s だが、`fdatasync/s` も 150,013 であり、1 commit 1 sync の設計が残っている。
7. P-WAL group commit は `fdatasync/s` を 20,022 まで減らし、commits/fdatasync を 7.78 まで上げる。
8. local-durable only upper bound は 189,002 tx/s と高速だが、一般の dependency を持つ workload では unsafe である。
9. straggler 実験では global prefix 版が 155,840 tx/s から 25,771 tx/s に落ちる一方、local-only upper bound は 144,390 tx/s を維持する。この差が global prefix の保守性を示す。
10. 研究の核心は、global durable prefix の安全性を保ったまま local-only upper bound に近づく dependency-closed durable frontier を設計することである。

## 13. Final Conclusion

本実験は、ERMIA の高並行 CC 自体よりも、WAL durability protocol が throughput を制限していることを示している。single WAL では shared mutex が bottleneck であり、P-WAL はそれを外せる。しかし P-WAL 後には `fdatasync` と durable notification / global prefix wait が支配的になる。

no-CC microbench により、durability だけでも同じ bottleneck が再現することが確認できた。さらに single WAL group commit baseline を入れることで、P-WAL の比較対象を naive 実装ではなく fair centralized WAL に引き上げた。この上で、P-WAL group commit と local-only upper bound の差、および straggler 実験の差から、global durable prefix が次の重要な bottleneck であることが分かる。

したがって、研究として最も筋がよい主張は次である。

```text
High-concurrency CC に durability を素朴に足すと、
CC conflict ではなく durability protocol が bottleneck になる。

P-WAL は shared WAL mutex を外すが、
その後は fdatasync と global durable prefix wait が支配的になる。

local-only durable wait は高速だが unsafe である。
安全性を保つには dependency を考慮した durable frontier が必要である。
```

## Artifacts

| artifact | path |
|---|---|
| this report source data | `docs/no_cc_wal_microbench_results_20260604.md` |
| no-CC CSV | `results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600.csv` |
| throughput graph | `results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_throughput_tps.svg` |
| closed-loop latency graph | `results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_closed_loop_avg_latency_us.svg` |
| fdatasync/s graph | `results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_fdatasync_per_sec.svg` |
| commits/fdatasync graph | `results/no_cc_wal_microbench_20260604_225600/no_cc_wal_microbench_20260604_225600_commits_per_fdatasync.svg` |
| perf stat directory | `results/no_cc_wal_perf_20260604_225600/` |
| ERMIA breakdown | `results/tx_breakdown_20260604_135550.txt` |
| ERMIA off-CPU Flame Graphs | `results/offcpu_flamegraph_20260604_1425/` |
