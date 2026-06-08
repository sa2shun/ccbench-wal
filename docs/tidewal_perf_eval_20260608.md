# TideWAL perf evaluation

date: 2026-06-08T17:05:16

This perf run uses only the three paper systems: Single WAL, P-WAL, and TideWAL.
It is internal analysis, not a component ablation.

## Conditions

| item | value |
|---|---|
| workloads | ycsb_a,ycsb_b,ycsb_c |
| worker threads | 32 |
| perf stat repeats | 3 |
| perf stat seconds | 5 |
| perf record seconds | 5 |
| events | task-clock,context-switches,cpu-migrations,page-faults,cycles,instructions,cache-references,cache-misses,LLC-loads,LLC-load-misses |
| stat csv | `results/tidewal_perf_eval_20260608_165844/tidewal_perf_stat_raw_20260608_165844.csv` |
| top csv | `paper/tables/tidewal_perf_top_20260608.csv` |

## perf stat summary

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 9759 | 1.20 | 306558 | 634424 | 2.069 | 127682 | 39030 | 1.00 | 0 | 4096 |
| YCSB-A | P-WAL | 69941 | 18.36 | 662955 | 392300 | 0.592 | 1418583 | 279538 | 1.00 | 0 | 512 |
| YCSB-A | TideWAL | 302880 | 24.15 | 196890 | 141190 | 0.717 | 3290211 | 84650 | 14.32 | 232 | 2048 |
| YCSB-B | Single WAL | 27139 | 1.08 | 99466 | 171821 | 1.727 | 136635 | 43670 | 2.49 | 0 | 4096 |
| YCSB-B | P-WAL | 255003 | 16.45 | 162643 | 103787 | 0.638 | 1394703 | 409551 | 2.49 | 0 | 256 |
| YCSB-B | TideWAL | 463454 | 24.35 | 128797 | 77435 | 0.601 | 3880368 | 133959 | 13.84 | 31 | 512 |
| YCSB-C | Single WAL | 1472816 | 40.06 | 70567 | 28147 | 0.399 | 3902 | 0 | 0.00 | 0 | 22 |
| YCSB-C | P-WAL | 1460392 | 40.06 | 71219 | 28155 | 0.396 | 3890 | 0 | 0.00 | 0 | 22 |
| YCSB-C | TideWAL | 1430557 | 40.00 | 72506 | 37032 | 0.511 | 91163 | 0 | 0.00 | 0 | 22 |

## perf top symbols

### YCSB-A

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 21.10 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 18.26 | `worker` |
| Single WAL | 3 | 7.01 | `YcsbWorkload::partTableInit<Tuple, void>` |
| Single WAL | 4 | 6.05 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 5 | 5.11 | `update_sg_lb_stats` |
| P-WAL | 1 | 39.58 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 10.12 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 3 | 4.58 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| P-WAL | 4 | 3.34 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 3.15 | `pthread_mutex_unlock@@GLIBC_2.2.5` |
| TideWAL | 1 | 6.32 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| TideWAL | 2 | 3.96 | `TxExecutor::abort` |
| TideWAL | 3 | 3.75 | `native_queued_spin_lock_slowpath.part.0` |
| TideWAL | 4 | 3.60 | `TxExecutor::ssn_parallel_commit` |
| TideWAL | 5 | 3.58 | `TxExecutor::mergeVersionFrontier` |

### YCSB-B

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 25.82 | `worker` |
| Single WAL | 2 | 19.12 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 4.52 | `TxExecutor::read_internal` |
| Single WAL | 4 | 3.41 | `scsi_target_queue_ready` |
| Single WAL | 5 | 3.39 | `update_sg_lb_stats` |
| P-WAL | 1 | 31.79 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 8.45 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 3 | 5.89 | `TxExecutor::read` |
| P-WAL | 4 | 4.79 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 3.99 | `worker` |
| TideWAL | 1 | 7.01 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| TideWAL | 2 | 6.04 | `TxExecutor::mergeVersionFrontier` |
| TideWAL | 3 | 5.52 | `native_queued_spin_lock_slowpath.part.0` |
| TideWAL | 4 | 4.55 | `MasstreeWrapper<Tuple>::get_value` |
| TideWAL | 5 | 4.27 | `TxExecutor::read` |

### YCSB-C

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 51.81 | `TxExecutor::read` |
| Single WAL | 2 | 16.13 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 8.02 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 5.42 | `TxExecutor::read_internal` |
| Single WAL | 5 | 4.90 | `TxExecutor::mainte` |
| P-WAL | 1 | 51.71 | `TxExecutor::read` |
| P-WAL | 2 | 15.77 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 6.99 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 4 | 5.54 | `TxExecutor::read_internal` |
| P-WAL | 5 | 5.11 | `TxExecutor::mainte` |
| TideWAL | 1 | 33.39 | `TxExecutor::read` |
| TideWAL | 2 | 11.05 | `TxExecutor::ssn_parallel_commit` |
| TideWAL | 3 | 10.97 | `TxExecutor::mergeVersionFrontier` |
| TideWAL | 4 | 7.58 | `MasstreeWrapper<Tuple>::get_value` |
| TideWAL | 5 | 6.73 | `pthread_mutex_lock@@GLIBC_2.2.5` |
