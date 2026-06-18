# Ayame perf evaluation

date: 2026-06-18T15:41:45

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
| stat csv | `results/ayame_perf_eval_20260618_153544/ayame_perf_stat_raw_20260618_153544.csv` |
| top csv | `paper/tables/ayame_perf_top_20260609.csv` |

## perf stat summary

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 8075 | 1.75 | 551502 | 1278817 | 2.319 | 132686 | 40383 | 1.00 | 0 | 8192 |
| YCSB-A | P-WAL | 48657 | 22.09 | 1154050 | 632178 | 0.548 | 1479910 | 243102 | 1.00 | 0 | 1024 |
| YCSB-A | Ayame | 237367 | 23.47 | 243197 | 154968 | 0.637 | 4207122 | 51741 | 22.97 | 318 | 2048 |
| YCSB-B | Single WAL | 22089 | 1.44 | 164677 | 278598 | 1.692 | 139784 | 44434 | 2.49 | 0 | 8192 |
| YCSB-B | P-WAL | 162556 | 18.04 | 282417 | 139875 | 0.495 | 1286655 | 326720 | 2.49 | 0 | 512 |
| YCSB-B | Ayame | 414409 | 24.32 | 144958 | 71294 | 0.492 | 4115618 | 144937 | 14.36 | 64 | 512 |
| YCSB-C | Single WAL | 680633 | 48.36 | 184346 | 29874 | 0.162 | 4169 | 0 | 0.00 | 0 | 70 |
| YCSB-C | P-WAL | 685480 | 48.35 | 183004 | 30282 | 0.165 | 4377 | 0 | 0.00 | 0 | 70 |
| YCSB-C | Ayame | 682952 | 48.41 | 183970 | 33567 | 0.183 | 34352 | 0 | 0.00 | 0 | 69 |

## perf top symbols

### YCSB-A

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 36.88 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 14.74 | `TxExecutor::install_version` |
| Single WAL | 3 | 7.58 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 6.75 | `TxExecutor::abort` |
| Single WAL | 5 | 4.13 | `update_sg_lb_stats` |
| P-WAL | 1 | 45.62 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 9.32 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 3 | 4.75 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| P-WAL | 4 | 3.30 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 2.86 | `TxExecutor::read` |
| Ayame | 1 | 5.72 | `native_queued_spin_lock_slowpath.part.0` |
| Ayame | 2 | 5.10 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 3 | 4.75 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 4 | 3.57 | `TxExecutor::abort` |
| Ayame | 5 | 3.39 | `TxExecutor::read` |

### YCSB-B

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 48.14 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 7.41 | `update_sg_lb_stats` |
| Single WAL | 3 | 4.03 | `TxExecutor::read_internal` |
| Single WAL | 4 | 3.59 | `_find_next_bit` |
| Single WAL | 5 | 3.19 | `dec_zone_page_state` |
| P-WAL | 1 | 36.88 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 15.52 | `TxExecutor::read` |
| P-WAL | 3 | 7.33 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 4 | 5.52 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 5 | 3.29 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 1 | 16.99 | `TxExecutor::read` |
| Ayame | 2 | 8.04 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 5.10 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 4 | 3.86 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 5 | 3.47 | `MasstreeWrapper<Tuple>::get_value` |

### YCSB-C

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 63.32 | `TxExecutor::read` |
| Single WAL | 2 | 23.95 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 3.55 | `TxExecutor::mainte` |
| Single WAL | 4 | 2.57 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 5 | 2.09 | `TxExecutor::read_internal` |
| P-WAL | 1 | 61.29 | `TxExecutor::read` |
| P-WAL | 2 | 24.99 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 3.39 | `TxExecutor::mainte` |
| P-WAL | 4 | 3.03 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 5 | 1.98 | `TxExecutor::read_internal` |
| Ayame | 1 | 60.61 | `TxExecutor::read` |
| Ayame | 2 | 24.09 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 3.28 | `TxExecutor::mainte` |
| Ayame | 4 | 2.71 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 5 | 2.00 | `TxExecutor::read_internal` |

