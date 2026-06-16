# Ayame perf evaluation

date: 2026-06-16T16:54:02

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
| stat csv | `results/ayame_perf_eval_20260616_164948/ayame_perf_stat_raw_20260616_164948.csv` |
| top csv | `paper/tables/ayame_perf_top_20260609.csv` |

## perf stat summary

| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 7467 | 1.33 | 472527 | 755683 | 1.621 | 125815 | 37332 | 1.00 | 0 | 4096 |
| YCSB-A | P-WAL | 42643 | 10.22 | 609702 | 517618 | 0.853 | 1119845 | 213049 | 1.00 | 0 | 1024 |
| YCSB-A | Ayame | 147570 | 18.58 | 315832 | 374322 | 1.171 | 1431732 | 52175 | 15.40 | 65512 | 262144 |
| YCSB-B | Single WAL | 18406 | 1.29 | 254884 | 207058 | 1.004 | 118337 | 36802 | 2.50 | 0 | 2048 |
| YCSB-B | P-WAL | 124350 | 8.99 | 213495 | 158618 | 0.786 | 956930 | 249403 | 2.49 | 0 | 512 |
| YCSB-B | Ayame | 507746 | 23.30 | 138704 | 121004 | 0.782 | 2377111 | 72627 | 34.79 | 6376 | 32768 |
| YCSB-C | Single WAL | 654653 | 32.36 | 128525 | 29586 | 0.231 | 3126 | 0 | 0.00 | 0 | 50 |
| YCSB-C | P-WAL | 629131 | 32.46 | 133945 | 30034 | 0.224 | 3405 | 0 | 0.00 | 0 | 51 |
| YCSB-C | Ayame | 659972 | 32.47 | 127754 | 33304 | 0.261 | 34728 | 0 | 0.00 | 0 | 49 |

## perf top symbols

### YCSB-A

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 18.60 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 15.85 | `TxExecutor::install_version` |
| Single WAL | 3 | 6.18 | `slab_free_freelist_hook.constprop.0` |
| Single WAL | 4 | 5.60 | `ttwu_queue_wakelist` |
| Single WAL | 5 | 5.34 | `ext4_sync_file` |
| P-WAL | 1 | 37.85 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 12.08 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 3 | 5.41 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| P-WAL | 4 | 3.36 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 2.48 | `pthread_mutex_unlock@@GLIBC_2.2.5` |
| Ayame | 1 | 5.68 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 2 | 5.05 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 3 | 3.76 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 4 | 3.19 | `TxExecutor::install_version` |
| Ayame | 5 | 2.88 | `TxExecutor::publishReadFrontier` |

### YCSB-B

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 34.38 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 2 | 8.89 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 3 | 5.19 | `std::chrono::_V2::steady_clock::now` |
| Single WAL | 4 | 4.14 | `TxExecutor::read_internal` |
| Single WAL | 5 | 3.87 | `crc32c_pcl_intel_update` |
| P-WAL | 1 | 22.34 | `native_queued_spin_lock_slowpath.part.0` |
| P-WAL | 2 | 7.62 | `TxExecutor::read` |
| P-WAL | 3 | 7.59 | `ccbench::WalLogger::logPerThread<std::vector<SetElement<Tuple>, std::allocator<SetElement<Tuple> > > >` |
| P-WAL | 4 | 7.53 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 5 | 6.63 | `pthread_mutex_lock@@GLIBC_2.2.5` |
| Ayame | 1 | 9.82 | `TxExecutor::read` |
| Ayame | 2 | 5.30 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 3.99 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 4 | 3.42 | `TxExecutor::mergeVersionFrontier` |
| Ayame | 5 | 3.30 | `TxExecutor::read_internal` |

### YCSB-C

| system | rank | self % | symbol |
|---|---:|---:|---|
| Single WAL | 1 | 58.81 | `TxExecutor::read` |
| Single WAL | 2 | 24.00 | `TxExecutor::ssn_parallel_commit` |
| Single WAL | 3 | 4.41 | `MasstreeWrapper<Tuple>::get_value` |
| Single WAL | 4 | 3.31 | `TxExecutor::mainte` |
| Single WAL | 5 | 3.02 | `TxExecutor::read_internal` |
| P-WAL | 1 | 58.62 | `TxExecutor::read` |
| P-WAL | 2 | 23.52 | `TxExecutor::ssn_parallel_commit` |
| P-WAL | 3 | 4.39 | `MasstreeWrapper<Tuple>::get_value` |
| P-WAL | 4 | 3.54 | `TxExecutor::mainte` |
| P-WAL | 5 | 3.37 | `TxExecutor::read_internal` |
| Ayame | 1 | 57.20 | `TxExecutor::read` |
| Ayame | 2 | 22.58 | `TxExecutor::ssn_parallel_commit` |
| Ayame | 3 | 3.87 | `MasstreeWrapper<Tuple>::get_value` |
| Ayame | 4 | 3.13 | `TxExecutor::mainte` |
| Ayame | 5 | 3.06 | `TxExecutor::read_internal` |

