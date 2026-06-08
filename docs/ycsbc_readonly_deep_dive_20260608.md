# YCSB-C read-only deep dive

date: 2026-06-08T23:39:57

YCSB-C is read-only, so read-only WAL skipping removes WAL persistence from all three systems.
This report re-runs Single WAL, P-WAL, and TideWAL and quantifies why TideWAL can still differ from P-WAL.

## Conditions

| item | value |
|---|---|
| binary | `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` |
| workload | YCSB-C, 100% read, 10 ops/tx |
| worker threads | 32 |
| repeats | 5 |
| seconds | 5 |
| read-only WAL skip | enabled for all systems |
| TideWAL mode | `async_dep_frontier_cstamp` |
| TideWAL flusher/logger threads | 7 |
| TideWAL committer threads | 1 |
| raw csv | `paper/tables/ycsbc_readonly_deep_dive_raw_20260608.csv` |
| summary csv | `paper/tables/ycsbc_readonly_deep_dive_summary_20260608.csv` |
| perf top csv | `paper/tables/ycsbc_readonly_deep_dive_perf_top_20260608.csv` |
| figure | `paper/figures/fig_ycsbc_readonly_deep_dive.pdf` |

## Summary

`WAL ack p99 us` is the WAL-layer durable-ack latency.  It is 0 here because read-only transactions take the no-WAL fast path.  For transaction latency, use `benchmark latency ns` and `closed-loop ns/tx`.

| system | tps | benchmark latency ns | closed-loop ns/tx | WAL ack p99 us | WAL bytes | fdatasync | pending | frontier collect ns/tx | cycles/tx | instr/tx | ctx switches |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Single WAL | 801708 | 49958.8 | 39967.4 | 0.0 | 0 | 0 | 0 | 0.0 | 129819 | 28266 | 4007 |
| P-WAL | 796269 | 50250.6 | 40200.8 | 0.0 | 0 | 0 | 0 | 0.0 | 130577 | 28276 | 4126 |
| TideWAL | 783287 | 51080.2 | 40864.5 | 0.0 | 0 | 0 | 0 | 2676.1 | 132769 | 36726 | 40176 |

## P-WAL vs TideWAL difference

- P-WAL throughput: 796269 tx/s
- TideWAL throughput: 783287 tx/s
- closed-loop service time: P-WAL 40200.8 ns/tx, TideWAL 40864.5 ns/tx
- service-time gap: 663.7 ns/tx
- benchmark latency gap: 829.6 ns/tx
- TideWAL frontier collection: 2676.1 ns/tx, or 267.6 ns/read op
- frontier collection / service-time gap: 4.03x
- extra cycles: 2193 cycles/tx
- extra instructions: 8450 instructions/tx
- TideWAL read-only fast path: 1.000 per tx
- TideWAL waitlist registration: 0.0 ns/tx
- TideWAL committer CPU: 67.7 ns/tx

Interpretation:

- WAL persistence is not the cause of the difference: WAL bytes and fdatasync counts are zero for all systems.
- TideWAL does not enter the dependency waitlist on this workload: pending is zero and waitlist registration is zero.
- TideWAL still collects read-side dependency frontiers to check whether versions read by a read-only transaction depend on non-durable writers.
- The measured frontier-collection cost is large enough to account for the P-WAL/TideWAL service-time gap. Because the benchmark is closed-loop and multithreaded, the counter is not expected to equal the throughput-derived gap exactly; it is a consistency check.

## perf top symbols

TideWAL `mergeVersionFrontier` self samples: 2.67%
TideWAL `pthread_mutex_lock` self samples: 3.37%

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 56.82 | `TxExecutor::read` |
| Single WAL | 2 | 23.32 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 4.15 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 3.02 | `TxExecutor::read_internal` |
| Single WAL | 5 | 2.93 | `TxExecutor::mainte` |
| Single WAL | 6 | 2.14 | `worker` |
| Single WAL | 7 | 1.16 | `TxExecutor::begin` |
| Single WAL | 8 | 0.82 | `0x00000000000006e5` |
| P-WAL | 1 | 58.88 | `TxExecutor::read` |
| P-WAL | 2 | 23.03 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 3.75 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 4 | 2.99 | `TxExecutor::mainte` |
| P-WAL | 5 | 2.83 | `TxExecutor::read_internal` |
| P-WAL | 6 | 1.95 | `worker` |
| P-WAL | 7 | 0.99 | `TxExecutor::begin` |
| P-WAL | 8 | 0.73 | `0x00000000000006e5` |
| TideWAL | 1 | 52.88 | `TxExecutor::read` |
| TideWAL | 2 | 20.92 | `TxExecutor::ssn_parallel_commit` |
| TideWAL | 3 | 3.89 | `MasstreeWrapper<Tuple>::get_value` |
| TideWAL | 4 | 3.37 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| TideWAL | 5 | 3.02 | `TxExecutor::read_internal` |
| TideWAL | 6 | 2.74 | `TxExecutor::mainte` |
| TideWAL | 7 | 2.67 | `TxExecutor::mergeVersionFrontier` |
| TideWAL | 8 | 1.26 | `0x00000000000006e5` |

## Conclusion

YCSB-C confirms that Single WAL, P-WAL, and TideWAL are all on read-only fast paths with no WAL persistence.
The remaining P-WAL/TideWAL gap is explained by TideWAL-specific read-side frontier bookkeeping, not by fdatasync or durable-ack wait.
The quantitative evidence is: zero fdatasync, zero pending/waitlist registration, nonzero frontier collection cost, and `mergeVersionFrontier` appearing in TideWAL's perf profile.
