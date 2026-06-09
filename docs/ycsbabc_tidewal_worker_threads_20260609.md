# YCSB-A/B/C worker-thread TideWAL comparison

date: 2026-06-09T15:42:57

This rerun uses worker threads on the x-axis. All three systems use the same `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` binary. Single WAL is selected by `CCBENCH_WAL_MODE=shared`, while P-WAL and TideWAL use `CCBENCH_WAL_MODE=per_thread`.

Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. TideWAL also applies the read-only SI fast path: once a transaction is known to be read-only, it skips durability frontier collection and uses the empty-frontier read-only ack path.

## Conditions

| item | value |
|---|---|
| workloads | ycsb_a,ycsb_b,ycsb_c |
| worker threads | 1,2,4,8,16,32 |
| repeats | 5 |
| seconds | 5 |
| TideWAL logger mapping | {1: 1, 2: 1, 4: 1, 8: 2, 16: 4, 32: 7, 48: 9, 96: 19} |
| TideWAL group_size | 16 |
| TideWAL flush_us | 50 |
| TideWAL max_pending | 65536 |

summary csv: `paper/tables/ycsbabc_tidewal_worker_threads_20260609.csv`
raw csv: `results/ycsbabc_tidewal_worker_threads_20260609_152001/ycsbabc_tidewal_worker_threads_raw_20260609_152001.csv`

## Figures

- `paper/figures/fig_ycsbabc_worker_threads_throughput.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_latency.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_pending.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_tidewal_speedup_vs_pwal.pdf`

## 32-worker summary

| workload | system | workers | WAL streams | flushers | committers | total active | ack tps | p99 us | pending | fdatasync count | commits/fdatasync | read-only ratio | frontier bytes/tx | WAL atomic/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 32 | 1 | 0 | 0 | 32 | 9898 | 4096 | 0 | 39582 | 1.00 | 0.001 | 0.0 | 6.006 |
| YCSB-A | P-WAL | 32 | 32 | 0 | 0 | 32 | 65906 | 512 | 0 | 263401 | 1.00 | 0.001 | 0.0 | 6.002 |
| YCSB-A | TideWAL | 32 | 7 | 7 | 1 | 40 | 298144 | 8192 | 703 | 82107 | 14.53 | 0.001 | 56.0 | 0.000 |
| YCSB-B | Single WAL | 32 | 1 | 0 | 0 | 32 | 28005 | 2048 | 0 | 45012 | 2.49 | 0.598 | 0.0 | 0.902 |
| YCSB-B | P-WAL | 32 | 32 | 0 | 0 | 32 | 237627 | 256 | 0 | 381791 | 2.49 | 0.598 | 0.0 | 0.902 |
| YCSB-B | TideWAL | 32 | 7 | 7 | 1 | 40 | 553176 | 512 | 58 | 119651 | 18.50 | 0.599 | 56.0 | 0.000 |
| YCSB-C | Single WAL | 32 | 1 | 0 | 0 | 32 | 780896 | 41 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | P-WAL | 32 | 32 | 0 | 0 | 32 | 804493 | 40 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | TideWAL | 32 | 7 | 7 | 1 | 40 | 782015 | 41 | 0 | 0 | 0.00 | 1.000 | 56.0 | 0.000 |
