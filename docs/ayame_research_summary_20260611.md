# Ayame 研究まとめ

date: 2026-06-11

この文書は、ERMIA/SSN と P-WAL を接続して高速な durable commit を実現する研究について、最初のボトルネック調査から現在の Ayame 論文状態までを一つにまとめたものです。

現在の名称は **Ayame** です。途中では `Cstamp-PWAL` や `TideWAL` と呼んでいましたが、論文・図表・評価では Ayame に統一しています。

## 1. 研究の目的

この研究の目的は、SSN/ERMIA のような高並行 MVCC/CC に durability を足したとき、WAL 側が新しいボトルネックにならないようにすることです。

最終的な研究主張は次です。

> ERMIA/SSN はすでに commit timestamp (`cstamp`) によって serialization order を持っている。したがって WAL 側で separate global LSN を再導入するのは二重管理である。Ayame は `cstamp` を logical LSN として再利用し、global durable prefix ではなく dependency-closed durable frontier によって durable acknowledgment を返す。さらに worker / flusher / committer を分離することで、worker を `fdatasync` や durable wait から外す。

評価上の主張は次です。

- Single WAL は shared logging bottleneck を作る。
- P-WAL は single WAL の mutex bottleneck を外すが、frequent small flush / kernel synchronization / per-transaction durable wait が残る。
- Ayame は group flush と async durable acknowledgment により update/mixed workload で P-WAL より高い durable-ack throughput を出す。
- Ayame は read-only workload では WAL persistence が消えるため、Single WAL / P-WAL とほぼ同等になるべきであり、現在の結果もそうなっている。
- Ayame の `cstamp` 統合は主に設計上の簡素化であり、単独の throughput 最適化としては主張しない。

## 2. 研究の流れ

### 2.1 最初の問題設定

最初は ERMIA に WAL/P-WAL durability を素朴に接続し、`normal_ycsb` / `abort0_ycsb` を 1, 2, 4, 8, 16, 32 threads で測りました。

この段階では自作 timer によって、以下のような処理時間を分解していました。

- read visible version
- update / version creation
- SSN finalize / exclusion check
- node validation
- WAL record construction
- WAL append / write
- `fdatasync`
- global durable prefix / notify wait
- version publish

この調査で、CC 本体よりも WAL durability 側が支配的になることが見えました。特に P-WAL でも `fdatasync` と durable wait が大きく、単に log stream を分けるだけでは十分ではありませんでした。

### 2.2 perf / Flame Graph への移行

自作 timer だけでは、mutex / futex / kernel scheduler / spinlock などのコストが見えにくいため、Linux `perf` と Flame Graph も使う方針に切り替えました。

この時点での重要な理解は次です。

- Single WAL は `lock -> write -> fdatasync -> unlock` のような形になりやすく、global WAL mutex が commit path を直列化する。
- P-WAL は shared WAL mutex を外せるが、各 log stream の `fdatasync` と durable wait が残る。
- thread 数を増やしても、storage sync capacity が増えるわけではない。
- worker が `fdatasync` や durable prefix を直接待つと、ERMIA の高並行性が失われる。

### 2.3 no-CC durability microbench

次に、CC conflict と durability bottleneck を分離するために no-CC WAL microbench を作りました。

目的は、transaction execution / Masstree access / validation / abort を外し、WAL durability だけの上限と詰まり方を見ることでした。

比較した代表的な mode は次です。

| mode | 目的 |
|---|---|
| no durability | durability なしの上限 |
| single WAL | shared WAL の素朴な baseline |
| P-WAL per-txn fdatasync | log stream 分散だけの効果 |
| P-WAL group commit | batching の効果 |
| P-WAL local-only / no-prefix | unsafe upper bound |
| P-WAL dependency frontier | global prefix を dependency frontier に置き換える方向 |

ここで重要だったのは、`pwal_group_commit_no_prefix` を単に「高速版」と呼んではいけないという点です。これは一般 transaction workload では安全ではないため、正しくは **local-durable-only unsafe upper bound** です。

理由は、transaction `T` が `U` に依存しているとき、`T` の local log だけが durable でも、`U` の log が durable でなければ crash recovery 後に `T` だけが残る可能性があるためです。

### 2.4 latency 計測の修正

初期の latency 表では、Single WAL の p50/p99 が closed-loop throughput と整合しない問題がありました。

例えば 32 threads で throughput が約 9,841 tx/s の場合、Little's law 的には平均 latency は次になります。

```text
32 / 9841 sec = 約 3.25 ms
```

しかし sampled p50/p99 が 100 us 程度になっていたため、sampled latency が mutex wait や durable wait 全体を含んでいない可能性がありました。

そこで、closed-loop average latency や durable ack latency を重視し、main metric を **durable-acknowledgment throughput** にしました。

### 2.5 fdatasync / storage microbench

「thread 数を増やすと本当に `fdatasync` が遅くなるのか」を CC から切り離して確認するため、独立した storage microbench も作りました。

代表的な結果は次です。

- `sync_every = 8 writes` では、4 threads で throughput が約 229K ops/s に達した。
- 8, 16, 32 threads に増やしても throughput はほぼ伸びなかった。
- 一方で avg `fdatasync` latency は 4 threads の約 128.9 us から、32 threads の約 1,096.8 us まで悪化した。

この結果から、flusher concurrency を増やしすぎても storage は速くならず、latency と scheduler/kernel synchronization cost だけが悪化する可能性があると分かりました。

この知見は、後の Ayame の worker/flusher 比率や group commit 設定に反映されています。

## 3. 理論整理

### 3.1 cstamp と LSN の関係

ERMIA/SSN では、transaction commit 時に `cstamp` が決まります。これは serialization order を表す commit timestamp です。

従来の WAL/P-WAL 実装では、これとは別に WAL 用の global LSN を持つことがあります。

```text
cstamp: CC / serialization order 用
LSN   : WAL / durability / recovery order 用
```

Ayame では、これを次のように整理します。

```text
cstamp      : logical LSN / serialization order / recovery replay order
local_seq   : 各 WAL shard 内の physical log position
dep frontier: durable ack に必要な dependency condition
```

重要なのは、物理ログ位置を消すわけではないという点です。各 WAL shard の `local_seq` や byte offset は、ログファイルを読むための physical address として残ります。消すのは WAL 側の separate global logical LSN です。

### 3.2 dependency と durable ack

transaction `T` が transaction `U` に依存することを `U -> T` とします。

このとき、正しい整理は次です。

| 関係 | 必要な条件 |
|---|---|
| `U` と `T` に依存関係がない | `cstamp` order も physical flush order もどちらでもよい |
| `U -> T` がある | `c(U) < c(T)` が必要 |
| physical flush order | `T` の log が `U` より先に flush されてもよい |
| durable ack order | `T` は `U` の log も durable になるまで ack してはいけない |

つまり、physical flush order は serialization order と一致しなくてよいが、durable acknowledgment は dependency-closed でなければなりません。

### 3.3 global prefix と dependency frontier

global prefix は安全ですが、保守的です。

```text
ack(T) if global durable prefix >= T.global_lsn
```

これは `T` と無関係な log shard が遅い場合でも `T` を待たせます。

Ayame では次に置き換えます。

```text
ack(T) if
  T 自身の log record が durable
  and
  T.dep に含まれる全 log position が durable
```

この条件を dependency-closed durable frontier と呼びます。

## 4. Ayame の設計

Ayame は次の3つの要素からなります。

### 4.1 Commit timestamp as logical LSN

Ayame は ERMIA/SSN がすでに持つ `cstamp` を logical LSN として使います。

これにより、WAL 側の separate global LSN allocation を消します。現在の評価では Ayame の `WAL atomic/tx` は 0 です。

ただし、これは「これだけで throughput が劇的に上がる」という主張ではありません。主な主張は、CC order と recovery logical order の二重管理を消すことです。

### 4.2 Worker / flusher / committer separation

Ayame は commit processing を3段に分けます。

| role | 仕事 |
|---|---|
| worker | transaction 実行、validation、cstamp 決定、WAL enqueue、frontier publish |
| flusher/logger | WAL shard ごとに batch write + `fdatasync`、`durable_seq` 更新 |
| committer | durable condition を満たした transaction に durable ack を返す |

worker は `fdatasync` を直接待ちません。logical commit 後、durable ack は committer が返します。

評価では throughput として worker の logical commit 数ではなく、**client に返した durable ack 数**を使います。

### 4.3 Dependency-closed durable acknowledgment

各 version には durability frontier metadata を持たせます。

```text
write_frontier: この version を作った transaction までに必要な durable frontier
read_frontier : この version を読んだ read-write transaction から future writer が保守的に継承する frontier
```

transaction execution 中に frontier を集めます。

```text
read(v):
  T.dep = max(T.dep, v.write_frontier)

overwrite(v):
  T.dep = max(T.dep, v.write_frontier)
  T.dep = max(T.dep, v.read_frontier)
```

commit 時には、自分の log position も含めた `closed_frontier` を作り、version に publish します。

### 4.4 現在の proposal algorithm

現在の paper の `03_proposal.tex` は、説明文を削って以下の3つの algorithm に絞っています。

| algorithm | 内容 |
|---|---|
| Algorithm 1: Dependency Collection | transaction execution 中に `T.dep` を作る |
| Algorithm 2: Transaction Commit | validation から WAL append、frontier publish、ack request 登録まで |
| Algorithm 3: Durable Acknowledgment | durable condition の登録、waitlist、ack 判定 |

flusher は algorithm ではなく本文説明に回す方針です。

```text
Each flusher owns one WAL shard.
It collects queued log records, writes them as a batch, calls fdatasync,
advances durable_seq[i], and notifies the committer.
```

## 5. 実装・評価 mode の変遷

### 5.1 途中で作った mode

no-CC / ERMIA 接続の途中では、以下のような mode を作って切り分けました。

| mode | 目的 |
|---|---|
| async_global_lsn_prefix | async pipeline でも global prefix がどう詰まるか |
| async_local_only_lsn | unsafe upper bound |
| async_dep_frontier_lsn | dependency frontier の効果 |
| async_dep_frontier_cstamp | cstamp-as-logical-LSN の効果 |
| pwal_group_dep_frontier | worker-wait 版 dependency frontier |

これらは研究設計を決めるには重要でしたが、現在の論文 main evaluation には出していません。

理由は、評価を複雑にしすぎず、最終的には次の3本だけで説明する方針にしたためです。

```text
Single WAL
P-WAL
Ayame
```

component ablation は main figure には出さず、counter と perf で内部分析します。

### 5.2 現在の main systems

現在の評価対象は次です。

| system | 意味 |
|---|---|
| Single WAL | 共有 WAL stream を使う baseline |
| P-WAL | worker ごとに WAL stream を分ける baseline |
| Ayame | dependency-closed async P-WAL + cstamp logical LSN |

すべて同じ `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` binary で切り替えます。

これは YCSB-C の調査で分かった重要な修正です。以前は別 binary / 別 commit path の差が混ざる危険がありました。現在は同一 binary に揃えています。

## 6. 実験条件

現在の main YCSB 評価条件は次です。

| item | value |
|---|---|
| workloads | YCSB-A, YCSB-B, YCSB-C |
| worker threads | 1, 2, 4, 8, 16, 32 |
| repeats | 5 |
| seconds | 5 |
| binary | `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` |
| Single WAL | `CCBENCH_WAL_MODE=shared` |
| P-WAL | `CCBENCH_WAL_MODE=per_thread` |
| Ayame group_size | 16 |
| Ayame flush_us | 50 |
| Ayame max_pending | 65536 |
| read-only WAL skip | enabled for all systems |
| Ayame read-only SI fast path | enabled |

Ayame の logger mapping は次です。

| worker threads | logger/flusher threads | committer threads |
|---:|---:|---:|
| 1 | 1 | 1 |
| 2 | 1 | 1 |
| 4 | 1 | 1 |
| 8 | 2 | 1 |
| 16 | 4 | 1 |
| 32 | 7 | 1 |

横軸は **transaction worker threads** です。Ayame は background flusher / committer threads を追加で使うため、表では `total active threads` も明記します。

## 7. 現在の主要結果

### 7.1 32-worker summary

最新の 32-worker 結果は次です。

| workload | system | ack tps | p99 us | pending | fdatasync | tx/sync | frontier B/tx | WAL atomic/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 9.9K | 4096 | 0 | 39582 | 1.0 | 0.0 | 6.01 |
| YCSB-A | P-WAL | 65.9K | 512 | 0 | 263401 | 1.0 | 0.0 | 6.00 |
| YCSB-A | Ayame | 298.1K | 8192 | 703 | 82107 | 14.5 | 56.0 | 0.00 |
| YCSB-B | Single WAL | 28.0K | 2048 | 0 | 45012 | 2.5 | 0.0 | 0.90 |
| YCSB-B | P-WAL | 237.6K | 256 | 0 | 381791 | 2.5 | 0.0 | 0.90 |
| YCSB-B | Ayame | 553.2K | 512 | 58 | 119651 | 18.5 | 56.0 | 0.00 |
| YCSB-C | Single WAL | 780.9K | 41 | 0 | 0 | 0.0 | 0.0 | 0.00 |
| YCSB-C | P-WAL | 804.5K | 40 | 0 | 0 | 0.0 | 0.0 | 0.00 |
| YCSB-C | Ayame | 782.0K | 41 | 0 | 0 | 0.0 | 56.0 | 0.00 |

### 7.2 P-WAL に対する speedup

| workload | P-WAL tps | Ayame tps | speedup |
|---|---:|---:|---:|
| YCSB-A | 65.9K | 298.1K | 4.52x |
| YCSB-B | 237.6K | 553.2K | 2.33x |
| YCSB-C | 804.5K | 782.0K | 0.97x |

abstract で使える現在の最大値は次です。

```text
Ayame achieves up to a 4.5-fold performance improvement over P-WAL in YCSB-A.
```

### 7.3 結果の読み方

YCSB-A は update-heavy なので WAL durability が強く効きます。P-WAL は Single WAL より速いですが、tx/sync は 1.0 で、small flush が多いです。Ayame は tx/sync を 14.5 まで増やし、fdatasync count を減らして throughput を上げています。

YCSB-B は read-heavy mixed workload です。Ayame は P-WAL より 2.33x 高い throughput を出しつつ、pending は 58 に抑えています。

YCSB-C は read-only です。read-only WAL skip により WAL persistence が消えるため、ここで大きな差が出るとむしろ問題です。現在は Single WAL / P-WAL / Ayame がほぼ同等で、sanity check として期待通りです。

## 8. perf 分析

### 8.1 perf stat 32-worker summary

`perf stat` による 32-worker の代表値は次です。

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | tx/sync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 9620 | 1.21 | 316310 | 648780 | 2.055 | 125696 | 38476 | 1.00 | 0 | 4096 |
| YCSB-A | P-WAL | 65159 | 19.51 | 760326 | 447343 | 0.589 | 1535950 | 260403 | 1.00 | 0 | 512 |
| YCSB-A | Ayame | 295683 | 25.67 | 216136 | 144297 | 0.667 | 2971234 | 81647 | 14.49 | 1131 | 8192 |
| YCSB-B | Single WAL | 27107 | 1.07 | 98355 | 157182 | 1.598 | 136108 | 43497 | 2.49 | 0 | 4096 |
| YCSB-B | P-WAL | 246623 | 18.63 | 191636 | 108816 | 0.569 | 1423509 | 396387 | 2.49 | 0 | 256 |
| YCSB-B | Ayame | 542500 | 31.15 | 145051 | 63165 | 0.436 | 2462420 | 123765 | 17.53 | 74 | 512 |
| YCSB-C | Single WAL | 791532 | 39.99 | 131145 | 28270 | 0.216 | 4927 | 0 | 0.00 | 0 | 41 |
| YCSB-C | P-WAL | 810788 | 40.03 | 128225 | 28264 | 0.221 | 4198 | 0 | 0.00 | 0 | 40 |
| YCSB-C | Ayame | 772297 | 40.17 | 135099 | 32551 | 0.241 | 35684 | 0 | 0.00 | 0 | 41 |

### 8.2 perf top から見える bottleneck

YCSB-A の P-WAL では、`native_queued_spin_lock_slowpath` が 39.07% で top になっています。これは P-WAL が single WAL mutex を外しても、kernel synchronization や flush path の競合が大きいことを示しています。

YCSB-A の Ayame では、top は `pthread_mutex_lock` 7.40%、`TxExecutor::mergeVersionFrontier` 6.11%、`TxExecutor::ssn_parallel_commit` 4.46% です。つまり Ayame では storage synchronization だけではなく、frontier metadata / queue / version publish 側に bottleneck が移っています。

YCSB-B の P-WAL では、`native_queued_spin_lock_slowpath` 25.30%、`TxExecutor::read` 14.45%、`WalLogger::logPerThread` 6.55% です。

YCSB-B の Ayame では、`TxExecutor::read` 19.42%、`TxExecutor::ssn_parallel_commit` 8.33%、`pthread_mutex_lock` 4.28% です。P-WAL より WAL flush pressure が下がり、read/SSN path と metadata/queue overhead が目立つようになります。

YCSB-C は read-only なので、すべての system で `TxExecutor::read` と `TxExecutor::ssn_parallel_commit` が支配的です。WAL persistence の評価ではなく、read-only fast path の sanity check です。

## 9. YCSB-C 問題と修正

途中で、YCSB-C は read-only のはずなのに Single WAL / P-WAL / Ayame の性能差が大きく出る問題がありました。

原因候補として以下がありました。

1. Single WAL / P-WAL / TideWAL が別 binary / 別 commit path になっていた。
2. Ayame が background flusher / committer thread を使うため、total active threads と worker threads の扱いが混ざっていた。
3. read-only transaction でも dependency frontier collect / ack bookkeeping をしていた。
4. empty frontier でも waitlist / registerDepAck path に入る可能性があった。

修正後の方針は次です。

- 3 system を同じ binary で切り替える。
- 横軸は worker threads にする。
- read-only WAL skip を全 system で有効にする。
- Ayame は read-only SI fast path を使い、read-only と分かった transaction は durability frontier collection と WAL append を省く。
- YCSB-C は durability performance ではなく sanity check として扱う。

現在の YCSB-C 32-worker 結果は次です。

| system | ack tps | p99 us | fdatasync | pending |
|---|---:|---:|---:|---:|
| Single WAL | 780.9K | 41 | 0 | 0 |
| P-WAL | 804.5K | 40 | 0 | 0 |
| Ayame | 782.0K | 41 | 0 | 0 |

この結果は、read-only workload で WAL persistence が消えていること、また Ayame の read-only overhead が大きくないことを示しています。

## 10. 論文で言えることと言えないこと

### 10.1 言えること

現在の結果から言えることは次です。

- Ayame は YCSB-A で P-WAL より 4.52x、YCSB-B で 2.33x 高い durable-ack throughput を出す。
- Single WAL は shared log bottleneck によって update workload で大きく遅い。
- P-WAL は Single WAL より速いが、frequent small flush と kernel synchronization が残る。
- Ayame は group flushing により commits/fdatasync を増やし、fdatasync pressure を下げる。
- Ayame は WAL-side separate global LSN allocation を消している。32-worker table では `WAL atomic/tx = 0.00`。
- YCSB-C は read-only WAL skip により 3 system がほぼ同等になり、read-only sanity check として妥当。

### 10.2 言わない方がよいこと

以下は現時点では強く主張しない方がよいです。

- 「Ayame は常に P-WAL より速い」
  - YCSB-C では read-only なので WAL persistence がなく、Ayame は P-WAL と同等か少し低い。

- 「cstamp 統合だけで throughput が上がる」
  - `cstamp` 統合の主張は、CC と WAL の logical order 二重管理を消す設計上の貢献。
  - throughput 改善の主因は、parallel logging / batching / async durable ack / dependency frontier。

- 「YCSB-C は durability performance を示す」
  - YCSB-C は WAL record が発生しないため、durability evaluation ではなく read-only fast path sanity check。

## 11. 現在の paper 構成

現在の論文ファイルは次です。

| file | 内容 |
|---|---|
| `paper/main.tex` | title / abstract / 全体構成 |
| `paper/sections/01_intro.tex` | motivation, problem, approach |
| `paper/sections/02_prep.tex` | background |
| `paper/sections/03_proposal.tex` | Ayame algorithms |
| `paper/sections/04_evaluation.tex` | 実験結果、図、表 |
| `paper/sections/05_related.tex` | related work |
| `paper/sections/06_concl.tex` | conclusion |

最新の実験 doc は次です。

| file | 内容 |
|---|---|
| `docs/ycsbabc_ayame_worker_threads_20260609.md` | YCSB-A/B/C worker-thread main results |
| `docs/ayame_perf_eval_20260609.md` | 32-worker perf stat / perf top |
| `docs/ycsbb_perf_scaling_20260609.md` | YCSB-B perf stat scaling |

最新の図は次です。

| file | 内容 |
|---|---|
| `paper/figures/fig1_ayame_ack_policies.pdf` | ack policy の概念図 |
| `paper/figures/fig_ycsbabc_worker_threads_throughput.pdf` | YCSB-A/B/C throughput |
| `paper/figures/fig_ycsbabc_worker_threads_latency.pdf` | YCSB-A/B/C p99 latency |
| `paper/figures/fig_ycsbabc_worker_threads_pending.pdf` | pending durable commits |
| `paper/figures/fig_ycsbabc_worker_threads_ayame_speedup_vs_pwal.pdf` | Ayame/P-WAL speedup |
| `paper/figures/fig_ycsbb_perf_scaling_tps.pdf` | YCSB-B perf scaling tps |
| `paper/figures/fig_ycsbb_perf_scaling_cpu_cores.pdf` | YCSB-B CPU cores |
| `paper/figures/fig_ycsbb_perf_scaling_context_switches.pdf` | YCSB-B context switches |
| `paper/figures/fig_ycsbb_perf_scaling_perf_stat.pdf` | YCSB-B perf stat summary |
| `paper/figures/fig_ycsbc_perf_top_symbols_horizontal.pdf` | YCSB-C top symbols |

最新の表は次です。

| file | 内容 |
|---|---|
| `paper/tables/table_ycsbabc_32worker_summary.tex` | 32-worker main result |
| `paper/tables/table_ayame_speedup_32worker.tex` | 32-worker Ayame/P-WAL speedup |
| `paper/tables/table_perf_stat_32worker.tex` | 32-worker perf stat |
| `paper/tables/table_ycsbb_perf_scaling.tex` | YCSB-B perf scaling |
| `paper/tables/table_ycsbc_perf_top.tex` | YCSB-C top symbols |

## 12. 現在の abstract に入る数値

現状の abstract で使うなら、次の書き方が妥当です。

```text
Ayame achieves up to a 4.5-fold performance improvement over P-WAL in YCSB-A.
```

根拠は次です。

```text
YCSB-A / 32 worker threads:
P-WAL  = 65.9K durable ack/s
Ayame  = 298.1K durable ack/s
speedup = 4.52x
```

YCSB-B では次です。

```text
YCSB-B / 32 worker threads:
P-WAL  = 237.6K durable ack/s
Ayame  = 553.2K durable ack/s
speedup = 2.33x
```

## 13. 残っている注意点

### 13.1 frontier metadata cost

Ayame は `frontier B/tx = 56.0` になっています。以前の 2048B/tx よりは大幅に軽くなりましたが、frontier merge / publish はまだ perf top に出ます。

今後さらに詰めるなら、以下が候補です。

- inline/small frontier representation
- object allocation 削減
- read frontier update の遅延・集約
- self-only dependency の fast path
- waitlist の batch processing

ただし、現在の論文では component ablation は出さず、perf/counter で説明する方針です。

### 13.2 worker thread と background thread

現在の main graph の横軸は worker threads です。Ayame は background flusher / committer を追加で使います。

これは論文中で必ず明記する必要があります。

```text
The x-axis is the number of transaction worker threads.
Ayame additionally runs background flusher/logger and committer threads.
```

32 workers では Ayame は 7 flusher threads + 1 committer thread を使うため、total active threads は 40 です。

### 13.3 correctness / recovery 実験

local-only が unsafe で、dependency frontier が safe であることは理論として説明しています。途中では deterministic recovery test も検討しましたが、現在の評価節には入れない方針です。

論文上は、correctness は theory / design section で扱い、evaluation は performance と internal bottleneck analysis に絞る方針です。

## 14. まとめ

この研究は、最初は「ERMIA と P-WAL をくっつけて速くする」から始まりましたが、調査を進める中で問題の中心は次の3つに整理されました。

1. ERMIA/SSN の `cstamp` と WAL の global LSN が logical order を二重管理している。
2. worker が `fdatasync` / durable prefix wait に巻き込まれると、CC の並行性が潰れる。
3. P-WAL の global durable prefix は安全だが保守的で、無関係な slow shard まで待つ。

Ayame はこれに対して次を行います。

1. `cstamp` を logical LSN として使う。
2. worker / flusher / committer を分離する。
3. global prefix ではなく dependency-closed durable frontier で ack する。

現在の実験では、Ayame は YCSB-A で P-WAL 比 4.52x、YCSB-B で 2.33x の durable-ack throughput を達成しています。YCSB-C では read-only WAL skip により3方式がほぼ同等で、これは read-only path の sanity check として妥当です。

したがって、現在の論文ストーリーは次の形に置くのが最も自然です。

> Single WAL は shared logging bottleneck を作る。P-WAL は log stream を分散するが、small flush と kernel synchronization が残る。Ayame は ERMIA/SSN の commit timestamp を logical LSN として再利用し、dependency-closed durable acknowledgment と async worker/flusher/committer pipeline によって、update/mixed workload の durable-ack throughput を改善する。

