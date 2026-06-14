# Ayame perf evaluation

date: 2026-06-14T12:50:51

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
| stat csv | `results/ayame_perf_eval_20260614_124642/ayame_perf_stat_raw_20260614_124642.csv` |
| top csv | `paper/tables/ayame_perf_top_20260609.csv` |

## perf stat summary

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 8109 | 1.14 | 352610 | 667554 | 1.893 | 132392 | 40532 | 1.00 | 0 | 4096 |
| YCSB-A | P-WAL | 54266 | 15.26 | 709234 | 411314 | 0.580 | 1546359 | 271090 | 1.00 | 0 | 512 |
| YCSB-A | Ayame | 247254 | 20.94 | 209634 | 139134 | 0.664 | 3356701 | 85167 | 14.53 | 773 | 4096 |
| YCSB-B | Single WAL | 22158 | 1.03 | 116358 | 161957 | 1.392 | 138839 | 44507 | 2.49 | 0 | 4096 |
| YCSB-B | P-WAL | 201666 | 14.62 | 183159 | 102454 | 0.559 | 1471140 | 404544 | 2.49 | 0 | 256 |
| YCSB-B | Ayame | 464925 | 25.39 | 137440 | 63130 | 0.460 | 2877998 | 126235 | 18.42 | 72 | 512 |
| YCSB-C | Single WAL | 658225 | 32.26 | 127166 | 29550 | 0.232 | 3297 | 0 | 0.00 | 0 | 49 |
| YCSB-C | P-WAL | 657812 | 32.26 | 127261 | 29959 | 0.235 | 3242 | 0 | 0.00 | 0 | 49 |
| YCSB-C | Ayame | 640848 | 32.28 | 130724 | 33253 | 0.255 | 34545 | 0 | 0.00 | 0 | 50 |

## perf top symbols

### YCSB-A

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 32.69 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 7.78 | `TxExecutor::install_version` |
| Single WAL | 3 | 6.04 | `do_syscall_64` |
| Single WAL | 4 | 5.80 | `TxExecutor::abort` |
| Single WAL | 5 | 5.58 | `__find_get_block` |
| P-WAL | 1 | 40.91 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 8.58 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 3 | 3.81 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| P-WAL | 4 | 3.71 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 2.76 | `pthread_mutex_unlock@@GLIBC_2.2.5` |
| Ayame | 1 | 8.88 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 2 | 6.16 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 3 | 3.38 | `TxExecutor::install_version` |
| Ayame | 4 | 3.18 | `TxExecutor::update` |
| Ayame | 5 | 3.00 | `TxExecutor::ssn_parallel_commit` |

### YCSB-B

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 26.92 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 6.53 | `update_sg_lb_stats` |
| Single WAL | 3 | 5.27 | `sd_setup_read_write_cmnd` |
| Single WAL | 4 | 5.25 | `idle_cpu` |
| Single WAL | 5 | 4.93 | `TxExecutor::read_internal` |
| P-WAL | 1 | 31.77 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 14.58 | `TxExecutor::read` |
| P-WAL | 3 | 7.46 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 4 | 7.04 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 4.01 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 1 | 20.85 | `TxExecutor::read` |
| Ayame | 2 | 9.83 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 5.01 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 4 | 4.93 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 5 | 3.91 | `MasstreeWrapper<Tuple>::get_value` |

### YCSB-C

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 58.74 | `TxExecutor::read` |
| Single WAL | 2 | 24.08 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 4.46 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 3.11 | `TxExecutor::read_internal` |
| Single WAL | 5 | 2.85 | `TxExecutor::mainte` |
| P-WAL | 1 | 60.25 | `TxExecutor::read` |
| P-WAL | 2 | 23.10 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 4.71 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 4 | 2.85 | `TxExecutor::mainte` |
| P-WAL | 5 | 2.55 | `TxExecutor::read_internal` |
| Ayame | 1 | 56.72 | `TxExecutor::read` |
| Ayame | 2 | 22.62 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 4.30 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 4 | 3.07 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 5 | 2.65 | `TxExecutor::read_internal` |

