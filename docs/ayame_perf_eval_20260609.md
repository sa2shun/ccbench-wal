# Ayame perf evaluation

date: 2026-06-13T17:32:57

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
| stat csv | `results/ayame_perf_eval_20260613_172838/ayame_perf_stat_raw_20260613_172838.csv` |
| top csv | `paper/tables/ayame_perf_top_20260609.csv` |

## perf stat summary

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 8089 | 1.15 | 358593 | 661657 | 1.845 | 131944 | 40434 | 1.00 | 0 | 4096 |
| YCSB-A | P-WAL | 54202 | 15.32 | 712565 | 407350 | 0.572 | 1545781 | 270771 | 1.00 | 0 | 512 |
| YCSB-A | Ayame | 245261 | 21.34 | 215653 | 139650 | 0.648 | 3406628 | 85497 | 14.34 | 174 | 4096 |
| YCSB-B | Single WAL | 22223 | 1.02 | 115584 | 160772 | 1.391 | 139201 | 44619 | 2.49 | 0 | 4096 |
| YCSB-B | P-WAL | 199555 | 14.34 | 181391 | 102440 | 0.565 | 1448383 | 400376 | 2.49 | 0 | 256 |
| YCSB-B | Ayame | 455033 | 25.38 | 140313 | 63232 | 0.451 | 2830611 | 126846 | 17.94 | 56 | 512 |
| YCSB-C | Single WAL | 654399 | 32.26 | 127980 | 29559 | 0.231 | 3420 | 0 | 0.00 | 0 | 49 |
| YCSB-C | P-WAL | 648120 | 32.26 | 129152 | 29967 | 0.232 | 3614 | 0 | 0.00 | 0 | 49 |
| YCSB-C | Ayame | 640535 | 32.28 | 130724 | 33257 | 0.254 | 34983 | 0 | 0.00 | 0 | 50 |

## perf top symbols

### YCSB-A

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 57.98 | `YcsbWorkload::partTableInit<Tuple, void>` |
| Single WAL | 2 | 9.14 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 2.56 | `worker` |
| Single WAL | 4 | 2.20 | `TxExecutor::install_version` |
| Single WAL | 5 | 2.07 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 1 | 36.58 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 11.50 | `YcsbWorkload::partTableInit<Tuple, void>` |
| P-WAL | 3 | 8.12 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 4 | 3.68 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| P-WAL | 5 | 3.55 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 1 | 9.47 | `YcsbWorkload::partTableInit<Tuple, void>` |
| Ayame | 2 | 6.72 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 3 | 6.49 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 4 | 3.33 | `native_queued_spin_lock_slowpath.part.0` |
| Ayame | 5 | 3.23 | `TxExecutor::ssn_parallel_commit` |

### YCSB-B

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 58.66 | `YcsbWorkload::partTableInit<Tuple, void>` |
| Single WAL | 2 | 8.53 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 4.28 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 3.95 | `worker` |
| Single WAL | 5 | 1.40 | `update_sg_lb_stats` |
| P-WAL | 1 | 21.88 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 13.73 | `YcsbWorkload::partTableInit<Tuple, void>` |
| P-WAL | 3 | 11.25 | `TxExecutor::read` |
| P-WAL | 4 | 7.07 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 5.24 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| Ayame | 1 | 15.32 | `TxExecutor::read` |
| Ayame | 2 | 9.62 | `YcsbWorkload::partTableInit<Tuple, void>` |
| Ayame | 3 | 7.38 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 4 | 4.13 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 5 | 3.89 | `MasstreeWrapper<Tuple>::get_value` |

### YCSB-C

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 53.00 | `TxExecutor::read` |
| Single WAL | 2 | 21.17 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 8.34 | `YcsbWorkload::partTableInit<Tuple, void>` |
| Single WAL | 4 | 3.98 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 5 | 3.10 | `TxExecutor::mainte` |
| P-WAL | 1 | 52.15 | `TxExecutor::read` |
| P-WAL | 2 | 22.19 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 8.33 | `YcsbWorkload::partTableInit<Tuple, void>` |
| P-WAL | 4 | 3.87 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 5 | 2.75 | `TxExecutor::read_internal` |
| Ayame | 1 | 51.35 | `TxExecutor::read` |
| Ayame | 2 | 20.64 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 8.69 | `YcsbWorkload::partTableInit<Tuple, void>` |
| Ayame | 4 | 3.27 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 5 | 2.82 | `TxExecutor::mergeVersionFrontier` |

