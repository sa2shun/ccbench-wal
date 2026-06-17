# Ayame perf evaluation

date: 2026-06-17T19:00:32

This perf run uses only the three paper systems: Single WAL, P-WAL, and Ayame.
It is internal analysis, not a component ablation.

## Conditions

| item | value |
|---|---|
| workloads | ycsb_a,ycsb_b,ycsb_c |
| worker threads | 48 |
| perf stat repeats | 3 |
| perf stat seconds | 5 |
| perf record seconds | 5 |
| events | task-clock,context-switches,cpu-migrations,page-faults,cycles,instructions,cache-references,cache-misses,LLC-loads,LLC-load-misses |
| stat csv | `results/ayame_perf_eval_20260617_185617/ayame_perf_stat_raw_20260617_185617.csv` |
| top csv | `paper/tables/ayame_perf_top_20260609.csv` |

## perf stat summary

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 8076 | 1.79 | 564282 | 1303384 | 2.311 | 132769 | 40390 | 1.00 | 0 | 8192 |
| YCSB-A | P-WAL | 48514 | 21.66 | 1135040 | 619897 | 0.546 | 1462609 | 242361 | 1.00 | 0 | 1024 |
| YCSB-A | Ayame | 235665 | 23.23 | 242291 | 155092 | 0.640 | 4220479 | 53203 | 22.16 | 326 | 2048 |
| YCSB-B | Single WAL | 22177 | 1.42 | 162121 | 272920 | 1.683 | 139877 | 44518 | 2.49 | 0 | 8192 |
| YCSB-B | P-WAL | 163460 | 18.00 | 280227 | 138314 | 0.494 | 1282460 | 327773 | 2.49 | 0 | 512 |
| YCSB-B | Ayame | 415976 | 24.14 | 143352 | 71344 | 0.498 | 4096198 | 147712 | 14.13 | 82 | 512 |
| YCSB-C | Single WAL | 689977 | 48.35 | 181836 | 29880 | 0.164 | 4163 | 0 | 0.00 | 0 | 69 |
| YCSB-C | P-WAL | 681803 | 48.36 | 184027 | 30295 | 0.165 | 4239 | 0 | 0.00 | 0 | 70 |
| YCSB-C | Ayame | 680646 | 48.41 | 184532 | 33551 | 0.182 | 34437 | 0 | 0.00 | 0 | 70 |

## perf top symbols

### YCSB-A

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 37.94 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 21.01 | `TxExecutor::install_version` |
| Single WAL | 3 | 6.53 | `update_sg_lb_stats` |
| Single WAL | 4 | 3.35 | `TxExecutor::abort` |
| Single WAL | 5 | 2.19 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 1 | 47.31 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 11.20 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 3 | 4.06 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| P-WAL | 4 | 3.19 | `TxExecutor::read` |
| P-WAL | 5 | 2.75 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 1 | 7.63 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 2 | 6.39 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 3 | 4.73 | `native_queued_spin_lock_slowpath.part.0` |
| Ayame | 4 | 3.13 | `TxExecutor::abort` |
| Ayame | 5 | 3.10 | `TxExecutor::ssn_parallel_commit` |

### YCSB-B

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 47.15 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 13.35 | `TxExecutor::read_internal` |
| Single WAL | 3 | 3.86 | `TxExecutor::read` |
| Single WAL | 4 | 3.76 | `__schedule` |
| Single WAL | 5 | 3.29 | `rq_qos_wait` |
| P-WAL | 1 | 35.92 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 14.79 | `TxExecutor::read` |
| P-WAL | 3 | 7.45 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 4 | 6.34 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 5 | 2.57 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 1 | 13.85 | `TxExecutor::read` |
| Ayame | 2 | 8.00 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 4.87 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 4 | 4.39 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 5 | 3.16 | `TxExecutor::publishReadFrontier` |

### YCSB-C

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 62.55 | `TxExecutor::read` |
| Single WAL | 2 | 24.45 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 3.09 | `TxExecutor::mainte` |
| Single WAL | 4 | 3.00 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 5 | 2.01 | `TxExecutor::read_internal` |
| P-WAL | 1 | 62.90 | `TxExecutor::read` |
| P-WAL | 2 | 23.56 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 3.14 | `TxExecutor::mainte` |
| P-WAL | 4 | 3.04 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 5 | 2.16 | `TxExecutor::read_internal` |
| Ayame | 1 | 60.41 | `TxExecutor::read` |
| Ayame | 2 | 23.24 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 3.31 | `TxExecutor::mainte` |
| Ayame | 4 | 3.03 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 5 | 2.13 | `TxExecutor::read_internal` |

