# Cstamp-PWAL: ERMIA の commit timestamp を logical LSN として使う dependency-closed P-WAL

date: 2026-06-07

branch: `cstamp-pwal-framework`

repository: `tx-playground/ccbench`

## 0. この文書の位置づけ

この文書は、これまでの WAL / P-WAL / ERMIA / Cstamp-PWAL 実験を、論文に近い形で 1 本にまとめたものです。

主目的は次の 3 つです。

1. なぜこの研究をやるのかを、実験数字付きで説明する。
2. 提案方式 Cstamp-PWAL が何を保証し、何を改善するのかを明確にする。
3. 現時点で言える主張と言ってはいけない主張を分ける。

重要な注意として、現時点の Cstamp-PWAL は「常に throughput が最大」という結果ではありません。現時点の一番強い主張は、`global LSN prefix` が作る巨大な durable-ack backlog と tail latency を、dependency frontier によって抑えられることです。

## 1. Draft Abstract

High-concurrency timestamp-based CC engines such as ERMIA already maintain a commit timestamp that defines the serialization order. However, when durability is added through conventional WAL or parallel WAL, the durability layer often reintroduces a separate global LSN and a conservative global durable prefix. Our measurements show that naive WAL turns into a shared mutex bottleneck, while P-WAL removes the mutex bottleneck but exposes storage synchronization and global-prefix waiting as the next bottleneck.

We propose Cstamp-PWAL, a durability protocol that reuses ERMIA's commit timestamp as a logical LSN and replaces global-prefix durable acknowledgment with dependency-closed durable frontier acknowledgment. Physical log positions remain local to each WAL shard. A three-stage worker/flusher/committer pipeline decouples logical commit from durable acknowledgment, allowing workers to continue transaction execution while flushers persist log batches and committers acknowledge only transactions whose own log and dependency frontier are durable.

On ERMIA/YCSB-B under a total-thread-budget comparison, async global LSN prefix reaches 354K durable acknowledgments/s at total=48, but it accumulates 65,498 pending commits and 131 ms p99 durable-ack latency. Cstamp-PWAL reaches 209K durable acknowledgments/s, but keeps pending commits to 49 and p99 latency to 512 us. Cstamp-PWAL also eliminates WAL-side separate global LSN allocation, reducing global WAL atomic operations from 1.5 per transaction to 0. These results suggest that the main benefit of dependency-closed Cstamp-PWAL is not unconditional peak throughput, but safe durable acknowledgment with much smaller backlog and tail latency when dependencies are sparse.

## 2. 研究の軸

既存 ERMIA/SSN 系 CC は commit timestamp `cstamp` を持っています。これは serialization order を表す logical timestamp です。

一方で、素朴に WAL/P-WAL durability を足すと、durability 側で別の global LSN と global durable prefix を導入しがちです。

```text
cstamp: CC / serialization order
LSN   : WAL / durability order
```

この二重管理により、次の問題が出ます。

1. single WAL では shared WAL mutex が serialization point になる。
2. P-WAL で WAL stream を分散しても、global durable prefix が slow logger に全体を引っ張る。
3. local-only durable wait は速いが、依存先 transaction の log durability を保証しないため一般には unsafe。

そこで研究の中心を次のように置きます。

```text
global durable prefix
  -> dependency-closed durable frontier

separate global WAL LSN
  -> ERMIA cstamp as logical LSN
```

ただし、物理ログ位置は消しません。各 WAL shard 内には `local_seq` / byte offset のような physical log position が必要です。

提案の整理は次です。

| 役割 | 提案方式での扱い |
|---|---|
| serialization order | ERMIA `cstamp` |
| recovery logical order | ERMIA `cstamp` を logical LSN として再利用 |
| physical log location | per-shard `local_seq` / file offset |
| durable ack condition | own log durable + dependency frontier durable |

## 3. Contributions

### Contribution 1: Commit timestamp as logical LSN

ERMIA は既に commit timestamp `cstamp` で serialization order を持っています。Cstamp-PWAL はこの `cstamp` を recovery logical order としても使います。

これにより、WAL 側で separate global LSN を割り当てる必要をなくします。

ただし、各 WAL shard の `local_seq` は残します。これは log file 内の物理位置であり、logical LSN ではありません。

### Contribution 2: Dependency-closed durable acknowledgment

global durable prefix は安全ですが保守的です。

従来型:

```text
ack(T) if durable_global_lsn >= g(T)
```

Cstamp-PWAL:

```text
ack(T) if
  T's own log position is durable
  and every log position in T.dep frontier is durable
```

これにより、T が依存していない slow logger shard を待つ必要がなくなります。

### Contribution 3: Worker / flusher / committer separation

worker が `fdatasync` や durable-prefix wait を直接待つと、ERMIA の高並行性が潰れます。

Cstamp-PWAL では commit pipeline を分離します。

```text
worker
  -> transaction execution
  -> validation
  -> WAL enqueue
  -> next transaction

flusher
  -> batch write
  -> fdatasync
  -> durable event queue

committer
  -> durable condition check
  -> client-visible durable ack
```

論文の main throughput は logical commit ではなく durable ack throughput で測ります。

## 4. Background: なぜ durability が bottleneck なのか

最初の ERMIA+WAL/P-WAL breakdown では、32 threads で次の結果でした。

| workload | protocol | throughput | abort rate | main bottleneck |
|---|---:|---:|---:|---|
| normal_ycsb | ERMIA+WAL | 8,512 tx/s | 0.0162 | shared WAL mutex |
| abort0_ycsb | ERMIA+WAL | 8,583 tx/s | 0.0000 | shared WAL mutex |
| normal_ycsb | ERMIA+P-WAL | 54,548 tx/s | 0.0155 | fdatasync + notify/global prefix wait |
| abort0_ycsb | ERMIA+P-WAL | 55,262 tx/s | 0.0000 | fdatasync + notify/global prefix wait |

abort0 workload にしても throughput はほとんど変わりません。

| protocol | normal_ycsb | abort0_ycsb | abort0 / normal |
|---|---:|---:|---:|
| ERMIA+WAL | 8,512 | 8,583 | 1.01x |
| ERMIA+P-WAL | 54,548 | 55,262 | 1.01x |

つまり、この段階で見えていた主因は CC conflict ではなく durability protocol です。

perf / Flame Graph でも同じ傾向です。

| workload | protocol | off-CPU total | futex | fdatasync | interpretation |
|---|---|---:|---:|---:|---|
| normal_ycsb | ERMIA+WAL | 96.299 s | 95.05% | 1.83% | shared WAL mutex wait |
| abort0_ycsb | ERMIA+WAL | 97.752 s | 95.03% | 1.90% | shared WAL mutex wait |
| normal_ycsb | ERMIA+P-WAL | 52.965 s | 3.16% | 86.55% | fdatasync/writeback wait |
| abort0_ycsb | ERMIA+P-WAL | 55.145 s | 4.23% | 85.64% | fdatasync/writeback wait |

この結果から、研究の動機は次になります。

```text
P-WAL は shared WAL mutex を外せる。
しかし、その後に fdatasync / durable point wait / global prefix wait が支配的になる。
```

## 5. no-CC durability microbench で見た WAL 単体の上限

ERMIA / Masstree / validation / abort / retry を外して、WAL durability だけを測りました。

条件:

| item | value |
|---|---|
| artifact | `results/no_cc_wal_microbench_20260604_225600/` |
| repeats | 5 |
| duration | 2 sec |
| threads | 1,2,4,8,16,32 |
| write set size | 10 |
| value size | 32 bytes |
| group size | 8 |
| flush interval | 100 us |
| I/O | buffered `write(2)` + `fdatasync(2)` |
| filesystem | ext4 |
| tmpfs | no |

32-thread result:

| mode | throughput | closed-loop avg latency | fdatasync/s | commits/fdatasync | main cost |
|---|---:|---:|---:|---:|---|
| no_durability | 8,676,499 | 3.7 us | 0 | 0 | log construction only |
| single_wal | 17,476 | 1,831.1 us | 17,476 | 1.00 | shared WAL mutex |
| single_wal_group_commit | 124,298 | 257.9 us | 7,757 | 16.02 | centralized group commit |
| pwal_per_txn_fdatasync | 150,013 | 213.3 us | 150,013 | 1.00 | per-txn fdatasync |
| pwal_group_commit | 155,840 | 205.6 us | 20,022 | 7.78 | durable point wait |
| pwal_group_commit_no_prefix | 189,002 | 169.3 us | 23,627 | 8.00 | local-only unsafe upper bound |

重要な読み方:

1. naive `single_wal` は 17.5K tx/s で止まり、mutex wait が約 1.77 ms/tx。
2. `single_wal_group_commit` は 124K tx/s まで上がる。したがって論文では fair centralized WAL baseline として必須。
3. P-WAL group commit は 156K tx/s だが、local-only upper bound は 189K tx/s。global prefix/durable wait がまだ効いている。
4. `pwal_group_commit_no_prefix` は fast だが unsafe。これは提案方式ではなく上限比較。

Straggler 実験では、global prefix の保守性がより明確です。

| mode | straggler | throughput | closed avg | logger local seq min/max | interpretation |
|---|---|---:|---:|---|---|
| pwal_group_commit | no | 155,840 | 205.6 us | 77,948 / 77,948 | safe global prefix |
| pwal_group_commit_no_prefix | no | 189,002 | 169.3 us | 94,193 / 94,996 | local-only upper bound |
| pwal_group_commit | logger 3 + 1 ms | 25,771 | 1,241.7 us | 12,890 / 12,890 | all loggers pulled by straggler |
| pwal_group_commit_no_prefix | logger 3 + 1 ms | 144,390 | 221.6 us | 12,832 / 92,881 | fast loggers continue, unsafe |

ここから、global prefix は safe だが conservative、local-only は fast だが unsafe、という構図が見えます。

## 6. Safety problem: local-only がなぜ unsafe か

local-only durable ack は、自分の logger shard だけを見ます。

```text
ack(T) if durable_seq[T.log_id] >= T.local_seq
```

しかし、次の依存があると壊れます。

```text
U writes x on logger 0
T reads x and writes y on logger 1

dependency:
  U -> T
```

このとき T の log が durable でも、U の log が durable でなければ T に ack を返してはいけません。crash 後に T だけ replay され、T が読んだ U が消える可能性があるためです。

したがって、正しい ack 条件は local-only ではなく dependency-closed である必要があります。

## 7. Cstamp-PWAL algorithm

### 7.1 Transaction descriptor

提案方式では、logical order と physical position を分けます。

```cpp
struct TxnDesc {
  uint64_t cstamp;      // logical LSN, also ERMIA commit timestamp
  uint32_t log_id;      // WAL shard
  uint64_t local_seq;   // physical position in the WAL shard
  Frontier dep;         // dependency durable frontier
  Payload log_record;
};
```

`cstamp` は logical order です。`local_seq` は physical log position です。

### 7.2 Dependency frontier

`Frontier[i]` は、transaction T に ack を返すために、WAL shard `i` が少なくともどこまで durable である必要があるかを表します。

```text
T.dep[i] = required durable local_seq on WAL shard i
```

ack 条件:

```text
durable_seq[T.log_id] >= T.local_seq
and
for all i:
  durable_seq[i] >= T.dep[i]
```

### 7.3 ERMIA での frontier collection

ERMIA の version に frontier pointer を追加しました。

```text
version.write_frontier
version.read_frontier
```

read 時:

```text
T.dep = max(T.dep, version.write_frontier)
```

write/delete 時:

```text
T.dep = max(T.dep, overwritten_version.write_frontier)
T.dep = max(T.dep, overwritten_version.read_frontier)
```

commit 後:

```text
closed = T.dep
closed[T.log_id] = max(closed[T.log_id], T.local_seq)

new written versions:
  write_frontier = closed

read versions:
  read_frontier = max(read_frontier, closed)
```

重要な ordering:

```text
WAL enqueue
  -> local_seq 取得
  -> closed frontier 作成
  -> version に frontier publish
  -> version を visible にする
  -> durable ack request 登録
```

visible な version に frontier が入っていない状態を作ると、後続 transaction が dependency を取りこぼします。

### 7.4 Worker / flusher / committer

Worker:

```text
execute tx
validate / SSN
assign cstamp
collect dependency frontier
build WAL record
enqueue to WAL shard
publish frontier to versions
register durable ack condition
continue next tx
```

Flusher:

```text
collect batch from shard queue
write records
fdatasync
push durable event to shard-local event queue
```

Committer:

```text
consume durable events
advance durable local_seq
advance global prefix if in global-prefix mode
pop waitlists whose conditions are satisfied
count durable acked commits
```

## 8. Correctness argument

### 8.1 Definitions

`U -> T` means T has a serialization/recovery dependency on U.

`c(T)` is T's commit timestamp.

`D(T)` is T's dependency frontier.

### 8.2 Assumptions

Assumption 1: Timestamp-order consistency

```text
if U -> T, then c(U) < c(T)
```

This is provided by ERMIA/SSN timestamp-based serialization.

Assumption 2: Sound dependency frontier

```text
if U is in transitive dependencies of T,
then U's log position is covered by D(T)
```

The frontier may over-approximate. It must not drop a required dependency.

### 8.3 Ack rule

Cstamp-PWAL returns durable ack for T only if:

```text
T's own log is durable
and
all log positions in D(T) are durable
```

### 8.4 Theorem 1: acknowledged transaction is recoverable

If T has received durable ack, then after a crash immediately after that ack, T can be recovered.

Proof sketch:

1. Ack rule says T's own log is durable.
2. Ack rule says every transitive dependency in D(T) is durable.
3. Recovery scans durable log prefixes and admits only dependency-closed transactions.
4. Therefore T and all needed dependencies are in the replay set.

### 8.5 Theorem 2: recovery order is serializable

If recovered transactions are replayed in increasing `cstamp` order, the recovered state respects serialization dependencies.

Proof sketch:

1. For every dependency edge `U -> T`, timestamp-order consistency gives `c(U) < c(T)`.
2. Recovery set is dependency-closed.
3. Replaying in increasing `cstamp` order respects all dependency edges.

### 8.6 Theorem 3: separate global logical LSN is not necessary

If `cstamp` extends dependency order and durable ack is dependency-closed, then a separate global logical WAL LSN prefix is not required for recovery correctness.

Global prefix is a sufficient condition:

```text
all previous global LSNs are durable
```

Cstamp-PWAL uses the necessary dependency condition:

```text
all dependency logs are durable
```

Physical log position remains local to each shard.

### 8.7 Deterministic correctness test

Implemented test:

```text
U writes x
T reads x and writes y
U -> T

physical flush order:
  T durable first
  U durable later
```

Result:

| mode | independent reversed flush | dependent reversed flush | transitive frontier | acked recovered | dependency violation |
|---|---|---|---|---|---|
| local-only | pass | fail | pass | no | yes |
| global-lsn-prefix | pass | pass | pass | yes | no |
| dep-frontier-lsn | pass | pass | pass | yes | no |
| dep-frontier-cstamp | pass | pass | pass | yes | no |

This is not a full crash-recovery implementation yet, but it validates the central dependency-closed durable ack condition.

## 9. Implemented modes

ERMIA に次の mode を接続しました。

| mode | meaning | role |
|---|---|---|
| `ermia_no_durability` | ERMIA without WAL | upper bound |
| `ermia_pwal_group_global_prefix` | worker-wait + group flusher + true global LSN prefix | old P-WAL baseline |
| `ermia_async_global_lsn_prefix` | async pipeline + true global LSN prefix | safe conservative baseline |
| `ermia_async_dep_frontier_lsn` | async + dependency frontier + separate global LSN | ablation without cstamp integration |
| `ermia_async_dep_frontier_cstamp` | async + dependency frontier + cstamp logical LSN | proposal |

Important metrics:

| metric | meaning |
|---|---|
| durable ack tps | `wal_stats_measured_acked_commits / actual_extime`; main throughput |
| logical tps | worker logical commit throughput; not the main metric |
| pending commits | logical commits not yet durably acked at measurement stop |
| p99 durable ack latency | enqueue-to-durable-ack tail latency |
| global atomic/tx | separate WAL global LSN allocation cost |
| frontier bytes/tx | dependency frontier metadata size |

## 10. Experiment setup

Hardware:

| item | value |
|---|---|
| CPU | Intel Xeon Gold 5418N |
| sockets | 2 |
| cores/socket | 24 |
| SMT | 2 threads/core |
| physical cores | 48 |
| logical CPUs | 96 |
| NUMA nodes | 2 |

Storage / filesystem from earlier WAL experiments:

| item | value |
|---|---|
| path | `/home/sa2shun/tx-playground/ccbench` |
| source | `/dev/mapper/vg0-lvhome[/sa2shun]` |
| filesystem | ext4 |
| tmpfs | no |
| I/O | buffered write + fdatasync |

Total-thread-budget policy:

```text
total active threads = worker + flusher/logger + committer
```

Async allocation:

| total | worker | flusher/logger | committer |
|---:|---:|---:|---:|
| 4 | 2 | 1 | 1 |
| 8 | 5 | 2 | 1 |
| 16 | 12 | 3 | 1 |
| 24 | 18 | 5 | 1 |
| 32 | 24 | 7 | 1 |
| 48 | 38 | 9 | 1 |
| 96 | 76 | 19 | 1 |

Pinning policy:

| total | CPU policy |
|---:|---|
| 1,2,4,8,16,24 | socket0 physical cores |
| 32,48 | all physical cores, one logical CPU per core |
| 96 | all logical CPUs including SMT |

`numactl --interleave=all` was used for total-budget runs.

## 11. Main result: YCSB-B total-thread-budget scaling

Workload:

```text
YCSB-B style
95% read / 5% update
10 ops/tx
repeats = 5
duration = 5 sec
```

Main table:

| total | mode | worker | flusher | committer | ack tps | p99 us | pending | fdatasync/s | atomic/tx | frontier bytes/tx |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | no durability | 1 | 0 | 0 | 327,991 | 0 | 0 | 0 | 0.000 | 0 |
| 2 | no durability | 2 | 0 | 0 | 575,551 | 0 | 0 | 0 | 0.000 | 0 |
| 4 | no durability | 4 | 0 | 0 | 1,012,901 | 0 | 0 | 0 | 0.000 | 0 |
| 4 | worker-wait global prefix | 2 | 1 | 1 | 10,141 | 256 | 0 | 5,071 | 1.496 | 0 |
| 4 | async global LSN prefix | 2 | 1 | 1 | 88,559 | 524,288 | 65,535 | 13,118 | 1.500 | 0 |
| 4 | async dep frontier cstamp | 2 | 1 | 1 | 89,174 | 524,288 | 63,025 | 13,116 | 0.000 | 8 |
| 8 | no durability | 8 | 0 | 0 | 1,791,380 | 0 | 0 | 0 | 0.000 | 0 |
| 8 | worker-wait global prefix | 5 | 2 | 1 | 24,616 | 256 | 0 | 9,863 | 1.500 | 0 |
| 8 | async global LSN prefix | 5 | 2 | 1 | 129,971 | 524,288 | 65,527 | 18,439 | 1.500 | 0 |
| 8 | async dep frontier cstamp | 5 | 2 | 1 | 131,257 | 524,288 | 65,533 | 18,559 | 0.000 | 16 |
| 16 | no durability | 16 | 0 | 0 | 2,844,026 | 0 | 0 | 0 | 0.000 | 0 |
| 16 | worker-wait global prefix | 12 | 3 | 1 | 54,351 | 256 | 0 | 14,120 | 1.501 | 0 |
| 16 | async global LSN prefix | 12 | 3 | 1 | 191,162 | 262,144 | 65,531 | 25,944 | 1.501 | 0 |
| 16 | async dep frontier cstamp | 12 | 3 | 1 | 196,824 | 235,930 | 59,207 | 26,455 | 0.000 | 24 |
| 24 | no durability | 24 | 0 | 0 | 3,445,190 | 0 | 0 | 0 | 0.000 | 0 |
| 24 | worker-wait global prefix | 18 | 5 | 1 | 72,408 | 256 | 0 | 22,634 | 1.500 | 0 |
| 24 | async global LSN prefix | 18 | 5 | 1 | 254,787 | 262,144 | 65,522 | 34,564 | 1.500 | 0 |
| 24 | async dep frontier cstamp | 18 | 5 | 1 | 234,823 | 53,862 | 10,578 | 30,229 | 0.000 | 40 |
| 32 | no durability | 32 | 0 | 0 | 4,106,446 | 0 | 0 | 0 | 0.000 | 0 |
| 32 | worker-wait global prefix | 24 | 7 | 1 | 88,546 | 307 | 0 | 30,866 | 1.500 | 0 |
| 32 | async global LSN prefix | 24 | 7 | 1 | 303,654 | 262,144 | 65,527 | 41,526 | 1.500 | 0 |
| 32 | async dep frontier cstamp | 24 | 7 | 1 | 233,782 | 512 | 58 | 33,287 | 0.000 | 56 |
| 48 | no durability | 48 | 0 | 0 | 5,407,325 | 0 | 0 | 0 | 0.000 | 0 |
| 48 | worker-wait global prefix | 38 | 9 | 1 | 122,725 | 512 | 0 | 38,966 | 1.500 | 0 |
| 48 | async global LSN prefix | 38 | 9 | 1 | 354,433 | 131,072 | 65,498 | 48,200 | 1.500 | 0 |
| 48 | async dep frontier cstamp | 38 | 9 | 1 | 208,827 | 512 | 49 | 39,212 | 0.000 | 72 |
| 96 | no durability | 96 | 0 | 0 | 1,314,004 | 0 | 0 | 0 | 0.000 | 0 |
| 96 | worker-wait global prefix | 76 | 19 | 1 | 170,986 | 512 | 0 | 63,911 | 1.500 | 0 |
| 96 | async global LSN prefix | 76 | 19 | 1 | 482,888 | 131,072 | 50,875 | 62,985 | 1.500 | 0 |
| 96 | async dep frontier cstamp | 76 | 19 | 1 | 192,209 | 512 | 57 | 70,720 | 0.000 | 152 |

### Interpretation

Worker-wait global prefix is slow even under total-thread-budget comparison. At total=48, it reaches 122K tx/s, while async global prefix reaches 354K tx/s and async dep frontier cstamp reaches 209K tx/s. This supports the worker/flusher/committer separation.

Async global prefix has high peak throughput, but it does so with a large durable-ack backlog. At total=48, pending is 65,498 and p99 is 131 ms.

Async dep frontier cstamp has lower peak throughput, but much smaller backlog and tail latency. At total=48, pending is 49 and p99 is 512 us.

Therefore the strongest current claim is:

```text
dependency frontier reduces global-prefix-induced durable-ack backlog and tail latency
for sparse-dependency workloads, while preserving safe dependency-closed ack.
```

## 12. Max-pending sweep

Condition:

```text
YCSB-B
total = 48
worker = 38
logger = 9
committer = 1
```

| mode | max_pending | ack tps | p99 us | pending | fdatasync/s |
|---|---:|---:|---:|---:|---:|
| async global LSN prefix | 1,024 | 360,354 | 3,686 | 1,038 | 47,428 |
| async global LSN prefix | 4,096 | 346,064 | 13,107 | 4,081 | 45,812 |
| async global LSN prefix | 16,384 | 355,730 | 45,875 | 16,352 | 47,089 |
| async global LSN prefix | 65,536 | 339,250 | 235,930 | 65,497 | 46,353 |
| async dep frontier cstamp | 1,024 | 209,847 | 512 | 52 | 38,773 |
| async dep frontier cstamp | 4,096 | 204,283 | 512 | 57 | 39,626 |
| async dep frontier cstamp | 16,384 | 211,863 | 512 | 48 | 39,492 |
| async dep frontier cstamp | 65,536 | 214,237 | 512 | 59 | 39,243 |

Global prefix keeps high throughput by allowing backlog. As `max_pending` increases, p99 worsens from 3.7 ms to 236 ms.

Dep frontier cstamp stays around 50 pending commits and 512 us p99 regardless of `max_pending`.

## 13. Cstamp vs separate LSN

Condition:

```text
YCSB-B
total = 48
worker = 38
logger = 9
committer = 1
```

| condition | mode | ack tps | p99 us | pending | atomic/tx | frontier bytes/tx | lsn alloc ns/tx |
|---|---|---:|---:|---:|---:|---:|---:|
| real I/O | dep frontier LSN | 209,401 | 512 | 48 | 1.500 | 72 | 227.4 |
| real I/O | dep frontier cstamp | 202,531 | 512 | 52 | 0.000 | 72 | 0.0 |
| I/O-light | dep frontier LSN | 210,800 | 1,024 | 112 | 1.500 | 72 | 219.5 |
| I/O-light | dep frontier cstamp | 205,525 | 1,024 | 96 | 0.000 | 72 | 0.0 |

This shows that cstamp integration eliminates WAL-side global LSN allocation:

```text
global atomic / tx: 1.5 -> 0
LSN allocation ns / tx: about 220 ns -> 0
```

However, it does not currently produce a large throughput win under total=48 YCSB-B. The correct claim is design simplification and removing duplicate global order allocation, not unconditional speedup.

## 14. Dense dependency case: YCSB-A

Condition:

```text
YCSB-A
total = 48
worker = 38
logger = 9
committer = 1
```

| mode | ack tps | p99 us | pending | fdatasync/s | atomic/tx | frontier bytes/tx |
|---|---:|---:|---:|---:|---:|---:|
| no durability | 3,792,252 | 0 | 0 | 0 | 0.000 | 0 |
| worker-wait global prefix | 82,393 | 512 | 0 | 29,708 | 6.001 | 0 |
| async global LSN prefix | 206,871 | 262,144 | 65,546 | 29,482 | 6.000 | 0 |
| async dep frontier cstamp | 202,043 | 262,144 | 65,517 | 28,656 | 0.000 | 72 |

YCSB-A has denser dependencies. In this case dep frontier approaches global prefix behavior, as expected. This is not a failure; it is the expected boundary condition of the design.

## 15. Why dep frontier is slower in peak throughput

At worker-fixed 32 threads / YCSB-B, dep frontier cstamp was slower than global prefix in peak ack throughput.

Counter breakdown showed the main reason is metadata propagation, not the waitlist itself.

Condition:

```text
YCSB-B
worker threads = 32
logger_num = 8
committer_num = 1
max_pending = 65,536
```

| component | global prefix | dep frontier cstamp | delta |
|---|---:|---:|---:|
| WAL enqueue ns/tx | 282 | 504 | +222 |
| frontier collect ns/tx | 0 | 47,892 | +47,892 |
| frontier merge ns/tx | 0 | 1,312 | +1,312 |
| frontier publish ns/tx | 0 | 97,628 | +97,628 |
| version install ns/tx | 144 | 143 | -1 |
| waitlist registration ns/tx | 47,854 | 8,968 | -38,885 |
| committer event ns/tx | 1,683 | 1,368 | -315 |
| write ns/tx | 7,098 | 5,693 | -1,405 |
| fdatasync ns/tx | 18,379 | 19,301 | +922 |
| queue wait us/acked tx | 221,018 | 329 | -220,689 |
| p99 durable ack us | 131,072 | 512 | -130,560 |

Ablation:

| mode | ack tps | p99 us | pending | interpretation |
|---|---:|---:|---:|---|
| global prefix | 372,279 | 131,072 | 65,531 | high throughput with backlog |
| dep frontier cstamp | 222,381 | 512 | 55 | safe frontier, low backlog |
| no publish | 370,213 | 524,288 | 65,487 | publish removed, throughput returns |
| zero dep | 360,341 | 314,573 | 65,537 | self-only waitlist is not main cost |
| prealloc | 226,195 | 512 | 56 | waitlist-time prealloc does not fix publish cost |

Conclusion:

```text
The throughput gap is dominated by frontier metadata publication and collection,
especially read_frontier updates and shared_ptr frontier management.
The durable waitlist itself is not the main overhead.
```

YCSB-B has 10 ops/tx and is read-heavy. The current implementation publishes `read_frontier` to many read versions. This costs around:

| metric | dep frontier cstamp |
|---|---:|
| frontier bytes/tx | 64 |
| read updates/tx | 9.50 |
| write updates/tx | 0.50 |
| frontier alloc/tx | 10.50 |
| shared_ptr/tx | 10.50 |
| waitlist registrations/tx | 1.00 |
| dependency conditions/tx | 1.00 |

This explains why Cstamp-PWAL has excellent p99/pending behavior but lower peak throughput in the current prototype.

## 16. What this paper can safely claim

Strong claims:

1. ERMIA+WAL is bottlenecked by shared WAL mutex, not CC conflict.
2. P-WAL removes the shared mutex bottleneck but exposes fdatasync and durable ordering as the next bottleneck.
3. A fair single WAL group commit baseline is necessary; naive single WAL is too weak.
4. Local-only durable ack is fast but unsafe.
5. Dependency-closed durable frontier is safe under the stated dependency-frontier soundness assumption.
6. Cstamp-PWAL can use ERMIA `cstamp` as logical LSN and eliminate separate WAL global LSN allocation.
7. Under YCSB-B total-budget comparison, dependency frontier dramatically reduces pending durable commits and p99 durable-ack latency compared with async global LSN prefix.
8. Under dense dependency workloads such as YCSB-A, dependency frontier naturally approaches global prefix behavior.

Claims to avoid:

1. Cstamp-PWAL is always faster than global prefix.
2. cstamp integration alone yields large throughput improvements under real I/O.
3. current frontier metadata implementation is optimized.
4. full crash recovery implementation is complete.

The paper should frame Cstamp-PWAL as:

```text
safe dependency-closed durable acknowledgment with cstamp-as-logical-LSN,
trading lower backlog/tail latency against current frontier metadata overhead.
```

## 17. Limitations

1. Full crash recovery replay is not implemented yet. Current correctness evidence is a deterministic dependency/durable-ack test.
2. The naive ERMIA frontier implementation is expensive. `read_frontier` publish and shared frontier object management dominate the throughput gap.
3. `results/` artifacts are local and ignored by Git. This document embeds key numbers so it remains readable after push.
4. total=96 includes SMT and shows strong interference. Main claims should focus on total=24/48, with 96 as robustness/appendix.
5. Current read-only transaction semantics are not fully separated in the paper story. For a final paper, read-only durable response semantics should be stated explicitly.

## 18. Paper figure plan

Minimum main-paper figures:

1. Bottleneck transition: ERMIA+WAL -> ERMIA+P-WAL -> no-CC WAL baselines.
2. YCSB-B total-budget throughput scaling.
3. YCSB-B total-budget p99 durable-ack latency.
4. YCSB-B total-budget pending durable commits.
5. Max-pending Pareto: throughput vs p99/pending.
6. Cstamp vs separate LSN: atomic/tx and LSN allocation cost.
7. Correctness table: local-only fails, global prefix / dep frontier LSN / dep frontier cstamp pass.

Appendix figures:

1. Worker-fixed comparison.
2. YCSB-A dense dependency result.
3. no-CC full scaling.
4. throughput gap breakdown / frontier metadata cost.
5. worker/flusher/committer ratio sweep.

## 19. Artifacts and reports

Tracked documents:

| document | purpose |
|---|---|
| `docs/wal_bottleneck_report_20260604.md` | ERMIA WAL/P-WAL bottleneck and no-CC WAL summary |
| `docs/no_cc_wal_microbench_results_20260604.md` | no-CC WAL durability microbench details |
| `docs/perf_flamegraph_results_20260604.md` | perf / Flame Graph setup and results |
| `docs/ermia_cstamp_pwal_modes.md` | implemented ERMIA Cstamp-PWAL modes |
| `docs/ermia_cstamp_pwal_bottleneck_report_20260606.md` | ERMIA Cstamp-PWAL worker-fixed report |
| `docs/ermia_cstamp_pwal_throughput_gap_breakdown_20260606.md` | why dep frontier peak throughput is lower |
| `docs/ermia_cstamp_pwal_total_thread_budget_report_20260607.md` | total-thread-budget result |
| `docs/cstamp_pwal_paper_draft_20260607.md` | this integrated paper-style report |

Local result directories:

| artifact | path |
|---|---|
| no-CC main result | `results/no_cc_wal_microbench_20260604_225600/` |
| ERMIA worker-fixed main result | `results/ermia_cstamp_pwal_20260606_162355_408754/` |
| throughput gap breakdown | `results/ermia_cstamp_pwal_20260606_184115_413672/` |
| total-budget YCSB-B | `results/ermia_cstamp_pwal_20260607_095421_116549/` |
| total=48 max-pending sweep | `results/ermia_cstamp_pwal_20260607_100737_898961/` |
| cstamp vs LSN total=48 | `results/ermia_cstamp_pwal_20260607_101135_857816/` |
| YCSB-A total=48 | `results/ermia_cstamp_pwal_20260607_101326_062322/` |

## 20. Reproduction commands

Build:

```bash
cmake --build build \
  --target ycsb_ermia_pwal.exe \
           ycsb_abort0_ermia_pwal.exe \
           ycsb_ermia.exe \
           ycsb_abort0_ermia.exe \
           ermia_cstamp_pwal_correctness_test.exe \
  -j2
```

Correctness:

```bash
./build/ermia_cstamp_pwal_correctness_test.exe
```

Total-budget YCSB-B:

```bash
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment total_budget \
  --seconds 5 \
  --repeats 5 \
  --total-values 1,2,4,8,16,24,32,48,96 \
  --total-workload ycsb_b \
  --skip-build
```

Max-pending sweep:

```bash
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment max_pending \
  --seconds 5 \
  --repeats 5 \
  --fixed-total-threads 48 \
  --pareto-workloads ycsb_b \
  --max-pending-values 1024,4096,16384,65536 \
  --skip-build
```

Cstamp vs LSN:

```bash
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment cstamp \
  --seconds 5 \
  --repeats 5 \
  --threads 48 \
  --fixed-total-threads 48 \
  --cstamp-workload ycsb_b \
  --skip-build
```

YCSB-A representative:

```bash
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment total_budget \
  --seconds 5 \
  --repeats 5 \
  --total-values 48 \
  --total-workload ycsb_a \
  --skip-build
```

## 21. Final paper story

The shortest accurate story is:

```text
ERMIA already has cstamp as serialization order.
Naively adding WAL introduces a shared WAL mutex bottleneck.
P-WAL removes that mutex, but global durable prefix remains conservative.
Local-only durable ack is fast but unsafe.
Cstamp-PWAL uses cstamp as logical LSN and acknowledges transactions only when
their dependency frontier is durable.
With worker/flusher/committer separation, workers do not wait directly for storage.
On sparse dependency workloads, Cstamp-PWAL avoids global-prefix-induced backlog
and tail latency while eliminating separate WAL global LSN allocation.
```

The honest conclusion is:

```text
Cstamp-PWAL is not yet a universal throughput win.
It is a safe dependency-aware durable ack design that converts a global-prefix
tail-latency/backlog problem into a metadata propagation problem.
The next optimization target is frontier metadata publication, not the durable
waitlist itself.
```

