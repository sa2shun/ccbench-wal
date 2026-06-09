# TideWAL perf evaluation

date: 2026-06-09T15:47:30

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
| stat csv | `results/tidewal_perf_eval_20260609_154309/tidewal_perf_stat_raw_20260609_154309.csv` |
| top csv | `paper/tables/tidewal_perf_top_20260609.csv` |

## perf stat summary

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 9620 | 1.21 | 316310 | 648780 | 2.055 | 125696 | 38476 | 1.00 | 0 | 4096 |
| YCSB-A | P-WAL | 65159 | 19.51 | 760326 | 447343 | 0.589 | 1535950 | 260403 | 1.00 | 0 | 512 |
| YCSB-A | TideWAL | 295683 | 25.67 | 216136 | 144297 | 0.667 | 2971234 | 81647 | 14.49 | 1131 | 8192 |
| YCSB-B | Single WAL | 27107 | 1.07 | 98355 | 157182 | 1.598 | 136108 | 43497 | 2.49 | 0 | 4096 |
| YCSB-B | P-WAL | 246623 | 18.63 | 191636 | 108816 | 0.569 | 1423509 | 396387 | 2.49 | 0 | 256 |
| YCSB-B | TideWAL | 542500 | 31.15 | 145051 | 63165 | 0.436 | 2462420 | 123765 | 17.53 | 74 | 512 |
| YCSB-C | Single WAL | 791532 | 39.99 | 131145 | 28270 | 0.216 | 4927 | 0 | 0.00 | 0 | 41 |
| YCSB-C | P-WAL | 810788 | 40.03 | 128225 | 28264 | 0.221 | 4198 | 0 | 0.00 | 0 | 40 |
| YCSB-C | TideWAL | 772297 | 40.17 | 135099 | 32551 | 0.241 | 35684 | 0 | 0.00 | 0 | 41 |

## perf top symbols

### YCSB-A

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 27.97 | `worker` |
| Single WAL | 2 | 11.99 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 8.43 | `TxExecutor::install_version` |
| Single WAL | 4 | 5.63 | `update_sg_lb_stats` |
| Single WAL | 5 | 2.77 | `TxExecutor::abort` |
| P-WAL | 1 | 39.07 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 10.70 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 3 | 3.50 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| P-WAL | 4 | 3.00 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 2.51 | `pthread_mutex_unlock@@GLIBC_2.2.5` |
| TideWAL | 1 | 7.40 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| TideWAL | 2 | 6.11 | `TxExecutor::mergeVersionFrontier` |
| TideWAL | 3 | 4.46 | `TxExecutor::ssn_parallel_commit` |
| TideWAL | 4 | 3.83 | `MasstreeWrapper<Tuple>::get_value` |
| TideWAL | 5 | 3.80 | `TxExecutor::install_version` |

### YCSB-B

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 12.44 | `worker` |
| Single WAL | 2 | 12.07 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 8.52 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 8.51 | `TxExecutor::read_internal` |
| Single WAL | 5 | 2.72 | `update_sg_lb_stats` |
| P-WAL | 1 | 25.30 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 14.45 | `TxExecutor::read` |
| P-WAL | 3 | 7.95 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 4 | 6.55 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 5 | 3.96 | `MasstreeWrapper<Tuple>::get_value` |
| TideWAL | 1 | 19.42 | `TxExecutor::read` |
| TideWAL | 2 | 8.33 | `TxExecutor::ssn_parallel_commit` |
| TideWAL | 3 | 4.55 | `TxExecutor::read_internal` |
| TideWAL | 4 | 4.28 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| TideWAL | 5 | 4.24 | `MasstreeWrapper<Tuple>::get_value` |

### YCSB-C

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 58.08 | `TxExecutor::read` |
| Single WAL | 2 | 23.07 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 4.22 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 3.17 | `TxExecutor::mainte` |
| Single WAL | 5 | 2.74 | `TxExecutor::read_internal` |
| P-WAL | 1 | 58.93 | `TxExecutor::read` |
| P-WAL | 2 | 22.93 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 3.93 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 4 | 3.13 | `TxExecutor::mainte` |
| P-WAL | 5 | 2.81 | `TxExecutor::read_internal` |
| TideWAL | 1 | 55.75 | `TxExecutor::read` |
| TideWAL | 2 | 21.67 | `TxExecutor::ssn_parallel_commit` |
| TideWAL | 3 | 4.08 | `MasstreeWrapper<Tuple>::get_value` |
| TideWAL | 4 | 3.21 | `TxExecutor::read_internal` |
| TideWAL | 5 | 3.03 | `TxExecutor::mainte` |

