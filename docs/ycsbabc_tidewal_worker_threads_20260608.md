# YCSB-A/B/C worker-thread TideWAL comparison

date: 2026-06-08T16:58:29

This rerun uses worker threads on the x-axis. All three systems use the same `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` binary. Single WAL is selected by `CCBENCH_WAL_MODE=shared`, while P-WAL and TideWAL use `CCBENCH_WAL_MODE=per_thread`.

Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. TideWAL additionally has an empty-frontier read-only fast path: if a read-only transaction has no durable dependency, it does not enter the dependency waitlist.

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

summary csv: `paper/tables/ycsbabc_tidewal_worker_threads_20260608.csv`
raw csv: `results/ycsbabc_tidewal_worker_threads_20260608_165827/ycsbabc_tidewal_worker_threads_raw_20260608_165827.csv`

## Figures

- `paper/figures/fig_ycsbabc_worker_threads_throughput.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_latency.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_pending.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_tidewal_speedup_vs_pwal.pdf`

## 32-worker summary

| workload | system | workers | WAL streams | flushers | committers | total active | ack tps | p99 us | pending | fdatasync count | commits/fdatasync | read-only ratio | frontier bytes/tx | WAL atomic/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 32 | 1 | 0 | 0 | 32 | 10082 | 4096 | 0 | 40320 | 1.00 | 0.001 | 0.0 | 6.005 |
| YCSB-A | P-WAL | 32 | 32 | 0 | 0 | 32 | 70360 | 512 | 0 | 281205 | 1.00 | 0.001 | 0.0 | 5.999 |
| YCSB-A | TideWAL | 32 | 7 | 7 | 1 | 40 | 303272 | 2048 | 221 | 84045 | 14.44 | 0.001 | 56.0 | 0.000 |
| YCSB-B | Single WAL | 32 | 1 | 0 | 0 | 32 | 28489 | 2048 | 0 | 45696 | 2.49 | 0.599 | 0.0 | 0.901 |
| YCSB-B | P-WAL | 32 | 32 | 0 | 0 | 32 | 251575 | 256 | 0 | 403855 | 2.49 | 0.599 | 0.0 | 0.901 |
| YCSB-B | TideWAL | 32 | 7 | 7 | 1 | 40 | 460460 | 512 | 59 | 130210 | 14.15 | 0.599 | 56.0 | 0.000 |
| YCSB-C | Single WAL | 32 | 1 | 0 | 0 | 32 | 1465872 | 22 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | P-WAL | 32 | 32 | 0 | 0 | 32 | 1457167 | 22 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | TideWAL | 32 | 7 | 7 | 1 | 40 | 1387033 | 23 | 0 | 0 | 0.00 | 1.000 | 56.0 | 0.000 |
