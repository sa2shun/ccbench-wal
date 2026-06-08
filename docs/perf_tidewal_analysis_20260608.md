# perf による TideWAL 分析

date: 2026-06-08

> Superseded note: この文書は YCSB-B/YCSB-C の原因調査用の古い補助メモです。
> 論文評価に使う perf/counter 結果は `docs/tidewal_perf_eval_20260608.md` と
> `docs/tidewal_evaluation_report_20260608.md` を参照してください。
> この文書には component diagnostic mode が含まれるため、main evaluation には使いません。

## 対象

直近の疑問に合わせて、次の 2 workload を `perf stat` / `perf record` で見た。

- YCSB-C: read-only fast path の overhead 確認
- YCSB-B: read-heavy + update 混在時の P-WAL / TideWAL bottleneck 確認

結果ディレクトリ:

```text
results/perf_ycsbc_20260608_154700
```

## YCSB-C: read-only workload

条件:

| item | value |
|---|---:|
| worker threads | 32 |
| extime | 10 sec for `perf stat`, 5 sec for `perf record` |
| ycsb_max_ope | 10 |
| read ratio | 100% |
| read-only WAL skip | enabled |
| TideWAL logger / committer | 7 / 1 |

### perf stat

| mode | ack tps | CPU util | cycles/tx | instr/tx | IPC | context switches | frontier collect ns/tx | committer CPU | committer idle |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P-WAL | 1,262,860 | 35.58 cores | 73,108 | 27,659 | 0.378 | 4,361 | 0.0 | 0 ms | 0 sec |
| TideWAL collect ON | 1,261,062 | 35.53 cores | 73,047 | 37,062 | 0.507 | 184,974 | 3,582.3 | 445 ms | 9.96 sec |
| TideWAL collect OFF diagnostic | 1,284,013 | 35.61 cores | 71,944 | 33,206 | 0.462 | 67,735 | 0.0 | 399 ms | 9.96 sec |

読み方:

- `perf stat` 上では P-WAL と TideWAL collect ON の throughput はほぼ同じ。
- TideWAL collect ON は P-WAL より instructions/tx が約 34% 多い。
- collect OFF diagnostic は instructions/tx を減らし、throughput は collect ON より約 1.8% 上がった。
- ただし collect OFF diagnostic は `CCBENCH_WAL_READ_ONLY_SKIP_FRONTIER_COLLECT=1` であり、correctness 用ではない。
- さらに skip 回数を数える atomic counter を read op ごとに増やしているので、production fast path の上限性能ではない。
- committer は 10 sec run で idle wait が約 9.96 sec、CPU time が約 0.4 sec。1 core を busy spin で握っている状態ではない。
- ただし context switches は TideWAL で多い。committer が `wait_for(100us)` で定期 wakeup している影響が見える。

### perf record: top symbols

P-WAL:

| self | symbol |
|---:|---|
| 52.58% | `TxExecutor::read` |
| 15.72% | `TxExecutor::ssn_parallel_commit` |
| 7.66% | `MasstreeWrapper<Tuple>::get_value` |
| 5.45% | `TxExecutor::read_internal` |
| 5.03% | `TxExecutor::mainte` |

TideWAL collect ON:

| self | symbol |
|---:|---|
| 37.23% | `TxExecutor::read` |
| 11.56% | `TxExecutor::ssn_parallel_commit` |
| 9.40% | `TxExecutor::mergeVersionFrontier` |
| 7.32% | `MasstreeWrapper<Tuple>::get_value` |
| 7.18% | `pthread_mutex_lock` |
| 4.73% | `TxExecutor::read_internal` |
| 0.78% | `ccbench::WalLogger::ackReadOnlyWithFrontier` |

TideWAL collect OFF diagnostic:

| self | symbol |
|---:|---|
| 41.32% | `TxExecutor::read` |
| 12.69% | `TxExecutor::ssn_parallel_commit` |
| 11.24% | `TxExecutor::mergeVersionFrontier` |
| 8.15% | `MasstreeWrapper<Tuple>::get_value` |
| 5.38% | `TxExecutor::read_internal` |
| 1.76% | `ccbench::WalLogger::ackReadOnlyWithFrontier` |

重要な点:

- TideWAL collect ON では `mergeVersionFrontier` が明確に出ている。
- その下に `pthread_mutex_lock` が出る。これは `std::atomic_load` on `shared_ptr` が内部 lock / refcount 系の処理を踏んでいる可能性が高い。
- collect OFF diagnostic でも `mergeVersionFrontier` は残る。これは frontier atomic load は skip しているが、毎 read の関数呼び出し、mode 判定、debug skip counter 更新は残るため。
- したがって「frontier collect が残っている」は正しいが、「残り差の主因が frontier collect だけ」とは言い切らない方がよい。

## YCSB-B: read-heavy + update workload

条件:

| item | value |
|---|---:|
| worker threads | 32 |
| extime | 10 sec for `perf stat`, 5 sec for `perf record` |
| ycsb_max_ope | 10 |
| read ratio | 95% |
| read-only WAL skip | enabled |
| TideWAL logger / committer | 7 / 1 |

### perf stat

| mode | ack tps | CPU util | cycles/tx | instr/tx | fdatasync count | commits/fdatasync | pending | p99 us | context switches |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P-WAL | 225,525 | 14.68 cores | 164,005 | 102,385 | 815,381 | 2.49 | 0 | 256 | 2,812,283 |
| TideWAL | 414,220 | 21.32 cores | 126,389 | 76,639 | 258,571 | 14.42 | 65 | 512 | 7,410,017 |

TideWAL の追加 counter:

| metric | value |
|---|---:|
| `frontier_collect_ns_per_tx` | 15.9 us |
| `frontier_publish_ns_per_tx` | 14.5 us |
| `waitlist_registration_ns_per_tx` | 32.0 us |
| `committer_cpu_ns` | 3.91 sec |
| `flusher_cpu_ns` | 17.50 sec |

読み方:

- YCSB-B では TideWAL が P-WAL の約 1.84x ack throughput。
- P-WAL は fdatasync count が多く、commits/fdatasync が 2.49 と小さい。
- TideWAL は group commit により commits/fdatasync が 14.42 まで上がる。
- TideWAL は CPU 利用が増えるが、cycles/tx と instr/tx は P-WAL より小さい。
- つまり、P-WAL は per-thread sync / kernel 側の待ちで詰まり、TideWAL は CPU を使って batching と durable ack pipeline を回している。

### perf record: top symbols

P-WAL:

| self | symbol |
|---:|---|
| 31.11% | `native_queued_spin_lock_slowpath.part.0` |
| 6.22% | `ccbench::WalLogger::logPerThread` |
| 6.16% | `TxExecutor::read` |
| 4.97% | `TxExecutor::ssn_parallel_commit` |
| 3.64% | `MasstreeWrapper<Tuple>::get_value` |
| 3.02% | `TxExecutor::read_internal` |
| 2.15% | `pthread_mutex_lock` |
| 1.77% | `__schedule` |
| 0.89% | `__sched_yield` |

P-WAL の読み方:

- top が kernel の `native_queued_spin_lock_slowpath`。
- per-transaction / small-batch fdatasync と複数 worker の同時 flush による kernel 側 lock contention が見えている。
- CPU profile だけを見ると user code より kernel synchronization が大きい。

TideWAL:

| self | symbol |
|---:|---|
| 7.28% | `pthread_mutex_lock` |
| 6.80% | `native_queued_spin_lock_slowpath.part.0` |
| 5.84% | `TxExecutor::mergeVersionFrontier` |
| 3.45% | `TxExecutor::publishReadFrontier` |
| 3.38% | `TxExecutor::ssn_parallel_commit` |
| 3.21% | `TxExecutor::read_internal` |
| 3.20% | `MasstreeWrapper<Tuple>::get_value` |
| 1.78% | `futex_wake` |
| 1.63% | `ccbench::WalLogger::registerDepAck` |
| 1.50% | `ccbench::WalLogger::asyncFlusherLoop` |

TideWAL の読み方:

- P-WAL の巨大な kernel spin は大きく減っている。
- 代わりに TideWAL 固有の frontier metadata と waitlist / queue 系が見えている。
- `mergeVersionFrontier`, `publishReadFrontier`, `registerDepAck` が次の最適化候補。
- `pthread_mutex_lock` / `futex_wake` も出ているため、frontier の `shared_ptr` atomic load/publish と waitlist/queue の lock が重い可能性が高い。

## 現時点の結論

### YCSB-C

言えること:

- read-only empty-frontier fast path は効いている。
- `registerDepAck` / waitlist overhead は消えている。
- TideWAL 固有 overhead として `mergeVersionFrontier` が perf に出る。
- その中には `atomic_load(shared_ptr)` 由来と思われる `pthread_mutex_lock` が含まれる。
- committer は 1 core を busy spin しているわけではない。ただし timeout wakeup による context switch は多い。

言い過ぎない方がいいこと:

- 「YCSB-C の残り差の主因は frontier collect」と断言すること。
- collect OFF diagnostic でも関数呼び出し / env 判定 / skip counter が残るため、完全な ablation ではない。

### YCSB-B

言えること:

- P-WAL は kernel spin / fdatasync 系で詰まっている。
- TideWAL は group commit により commits/fdatasync を増やし、ack throughput を上げている。
- TideWAL の次の bottleneck は storage そのものだけでなく、frontier metadata と waitlist/queue 管理に移っている。

## 次に効きそうな改善

1. `dependencyFrontierRequested()` / debug env 判定を read path で毎回 `getenv` しない
   - configure 時に bool として cache する。
   - `perf` 上で `getenv` / `parseDurableMode` が read path に出ている。

2. read-only transaction では frontier collect をまとめる / fast path 化する
   - correctness を保つなら、collect 自体は必要。
   - ただし version ごとの `atomic_load(shared_ptr)` を毎 read で踏む設計は重い。

3. `shared_ptr<const WalFrontier>` の atomic load/publish を軽くする
   - inline small frontier
   - epoch/RCU 的な pointer 管理
   - logger_num-sized fixed frontier
   - object pool より前に、atomic shared_ptr の lock/refcount cost を避ける方が効きそう。

4. committer の idle wait を timeout poll から event-driven に寄せる
   - YCSB-C で context switches が増えている。
   - `wait_for(100us)` を無限 wait + notify に変えられるか確認する。
   - shutdown / missed notify の扱いだけ注意。

5. waitlist / queue の lock を減らす
   - YCSB-B では `registerDepAck`, `pthread_mutex_lock`, `futex_wake` が見えている。
   - self-only / empty-dep fast path、batch ack、per-shard monotonic queue が候補。
