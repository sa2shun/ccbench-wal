# YCSB-A/B/C worker-thread Ayame comparison

date: 2026-06-16T16:14:59

This rerun uses worker threads on the x-axis. All three systems use the same `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` binary. Single WAL is selected by `CCBENCH_WAL_MODE=shared`, while P-WAL and Ayame use `CCBENCH_WAL_MODE=per_thread`.

Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. Ayame also applies the read-only SI fast path: once a transaction is known to be read-only, it skips durability frontier collection and uses the empty-frontier read-only ack path.

## Conditions

| item | value |
|---|---|
| workloads | ycsb_a,ycsb_b,ycsb_c |
| worker threads | 1,2,4,8,16,32,48,96 |
| repeats | 5 |
| seconds | 5 |
| Ayame logger mapping | {1: 1, 2: 1, 4: 1, 8: 2, 16: 4, 32: 7, 48: 9, 96: 19} |
| Ayame group_size | 16 |
| Ayame flush_us | 50 |
| Ayame max_pending | 65536 |

summary csv: `paper/tables/ycsbabc_ayame_worker_threads_20260609.csv`
raw csv: `results/ycsbabc_ayame_worker_threads_20260616_154407/ycsbabc_ayame_worker_threads_raw_20260616_154407.csv`

## Figures

- `paper/figures/fig_ycsbabc_worker_threads_throughput.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_latency.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_pending.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_ayame_speedup_vs_pwal.pdf`

## 32-worker summary

| workload | system | workers | WAL streams | flushers | committers | total active | ack tps | p99 us | pending | fdatasync count | commits/fdatasync | read-only ratio | frontier bytes/tx | WAL atomic/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 32 | 1 | 0 | 0 | 32 | 8493 | 4096 | 0 | 42451 | 1.00 | 0.001 | 0.0 | 6.002 |
| YCSB-A | P-WAL | 32 | 32 | 0 | 0 | 32 | 57946 | 512 | 0 | 289466 | 1.00 | 0.001 | 0.0 | 5.999 |
| YCSB-A | Ayame | 32 | 7 | 7 | 1 | 40 | 252963 | 16384 | 1657 | 85424 | 14.83 | 0.001 | 56.0 | 0.000 |
| YCSB-B | Single WAL | 32 | 1 | 0 | 0 | 32 | 24508 | 2048 | 0 | 49310 | 2.49 | 0.598 | 0.0 | 0.903 |
| YCSB-B | P-WAL | 32 | 32 | 0 | 0 | 32 | 199933 | 256 | 0 | 400993 | 2.49 | 0.599 | 0.0 | 0.901 |
| YCSB-B | Ayame | 32 | 7 | 7 | 1 | 40 | 442353 | 512 | 75 | 105638 | 21.32 | 0.599 | 56.0 | 0.000 |
| YCSB-C | Single WAL | 32 | 1 | 0 | 0 | 32 | 637869 | 50 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | P-WAL | 32 | 32 | 0 | 0 | 32 | 711613 | 48 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | Ayame | 32 | 7 | 7 | 1 | 40 | 680360 | 49 | 0 | 0 | 0.00 | 1.000 | 56.0 | 0.000 |
