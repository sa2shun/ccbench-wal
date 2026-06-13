# Ayame perf evaluation

date: 2026-06-13T15:01:55

This perf run uses only the three paper systems: Single WAL, P-WAL, and Ayame.
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
| stat csv | `results/ayame_perf_eval_20260611_211539/ayame_perf_stat_raw_20260611_211539.csv` |
| top csv | `paper/tables/ayame_perf_top_20260609.csv` |

## perf stat summary

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 9793 | 1.21 | 308526 | 674082 | 2.185 | 128118 | 39162 | 1.00 | 0 | 4096 |
| YCSB-A | P-WAL | 68484 | 18.97 | 698368 | 415055 | 0.594 | 1537033 | 273702 | 1.00 | 0 | 512 |
| YCSB-A | Ayame | 310602 | 26.75 | 213987 | 139150 | 0.650 | 3203414 | 84494 | 14.71 | 278 | 8192 |
| YCSB-B | Single WAL | 27178 | 1.07 | 98314 | 165785 | 1.686 | 136497 | 43622 | 2.49 | 0 | 4096 |
| YCSB-B | P-WAL | 249511 | 17.99 | 182056 | 103894 | 0.571 | 1417917 | 400741 | 2.49 | 0 | 256 |
| YCSB-B | Ayame | 571059 | 31.19 | 137447 | 63383 | 0.461 | 2752179 | 121713 | 18.79 | 65 | 512 |
| YCSB-C | Single WAL | 796029 | 40.06 | 130591 | 28272 | 0.217 | 3817 | 0 | 0.00 | 0 | 40 |
| YCSB-C | P-WAL | 793143 | 40.07 | 131230 | 28278 | 0.216 | 4033 | 0 | 0.00 | 0 | 40 |
| YCSB-C | Ayame | 810124 | 40.09 | 128448 | 32517 | 0.253 | 35374 | 0 | 0.00 | 0 | 39 |

## perf top symbols

### YCSB-A

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 25.49 | `worker` |
| Single WAL | 2 | 13.99 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 7.01 | `TxExecutor::install_version` |
| Single WAL | 4 | 6.55 | `TxExecutor::abort` |
| Single WAL | 5 | 2.90 | `0x000000000012e93d` |
| P-WAL | 1 | 39.80 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 9.92 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 3 | 3.46 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 4 | 2.76 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| P-WAL | 5 | 2.69 | `TxExecutor::update` |
| Ayame | 1 | 6.89 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 2 | 6.74 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 3 | 3.12 | `TxExecutor::mergeVersionReadFrontier` |
| Ayame | 4 | 3.10 | `native_queued_spin_lock_slowpath.part.0` |
| Ayame | 5 | 3.03 | `TxExecutor::ssn_parallel_commit` |

### YCSB-B

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 27.53 | `worker` |
| Single WAL | 2 | 12.63 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 6.30 | `TxExecutor::read_internal` |
| Single WAL | 4 | 4.51 | `plist_add` |
| Single WAL | 5 | 4.30 | `TxExecutor::read` |
| P-WAL | 1 | 24.22 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 17.02 | `TxExecutor::read` |
| P-WAL | 3 | 7.16 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 4 | 6.74 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 5 | 2.75 | `pthread_mutex_unlock@@GLIBC_2.2.5` |
| Ayame | 1 | 17.35 | `TxExecutor::read` |
| Ayame | 2 | 8.47 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 4.52 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 4 | 4.36 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 5 | 4.17 | `TxExecutor::read_internal` |

### YCSB-C

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 57.83 | `TxExecutor::read` |
| Single WAL | 2 | 22.89 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 4.09 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 3.25 | `TxExecutor::mainte` |
| Single WAL | 5 | 3.01 | `TxExecutor::read_internal` |
| P-WAL | 1 | 58.23 | `TxExecutor::read` |
| P-WAL | 2 | 22.54 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 3.64 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 4 | 3.43 | `TxExecutor::mainte` |
| P-WAL | 5 | 2.90 | `TxExecutor::read_internal` |
| Ayame | 1 | 55.20 | `TxExecutor::read` |
| Ayame | 2 | 21.68 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 4.25 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 4 | 3.11 | `TxExecutor::mainte` |
| Ayame | 5 | 2.98 | `TxExecutor::read_internal` |

