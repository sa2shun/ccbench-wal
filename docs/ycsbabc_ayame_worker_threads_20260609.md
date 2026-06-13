# YCSB-A/B/C worker-thread Ayame comparison

date: 2026-06-13T17:28:37

This rerun uses worker threads on the x-axis. All three systems use the same `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` binary. Single WAL is selected by `CCBENCH_WAL_MODE=shared`, while P-WAL and Ayame use `CCBENCH_WAL_MODE=per_thread`.

Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. Ayame also applies the read-only SI fast path: once a transaction is known to be read-only, it skips durability frontier collection and uses the empty-frontier read-only ack path.

## Conditions

| item | value |
|---|---|
| workloads | ycsb_a,ycsb_b,ycsb_c |
| worker threads | 1,2,4,8,16,32 |
| repeats | 5 |
| seconds | 5 |
| Ayame logger mapping | {1: 1, 2: 1, 4: 1, 8: 2, 16: 4, 32: 7, 48: 9, 96: 19} |
| Ayame group_size | 16 |
| Ayame flush_us | 50 |
| Ayame max_pending | 65536 |

summary csv: `paper/tables/ycsbabc_ayame_worker_threads_20260609.csv`
raw csv: `results/ycsbabc_ayame_worker_threads_20260613_170543/ycsbabc_ayame_worker_threads_raw_20260613_170543.csv`

## Figures

- `paper/figures/fig_ycsbabc_worker_threads_throughput.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_latency.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_pending.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_ayame_speedup_vs_pwal.pdf`

## 32-worker summary

| workload | system | workers | WAL streams | flushers | committers | total active | ack tps | p99 us | pending | fdatasync count | commits/fdatasync | read-only ratio | frontier bytes/tx | WAL atomic/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 32 | 1 | 0 | 0 | 32 | 8647 | 4096 | 0 | 43222 | 1.00 | 0.001 | 0.0 | 6.004 |
| YCSB-A | P-WAL | 32 | 32 | 0 | 0 | 32 | 54057 | 512 | 0 | 270064 | 1.00 | 0.001 | 0.0 | 6.000 |
| YCSB-A | Ayame | 32 | 7 | 7 | 1 | 40 | 253291 | 2048 | 201 | 88446 | 14.32 | 0.001 | 56.0 | 0.000 |
| YCSB-B | Single WAL | 32 | 1 | 0 | 0 | 32 | 24359 | 2048 | 0 | 48911 | 2.49 | 0.599 | 0.0 | 0.902 |
| YCSB-B | P-WAL | 32 | 32 | 0 | 0 | 32 | 195440 | 256 | 0 | 392489 | 2.49 | 0.598 | 0.0 | 0.902 |
| YCSB-B | Ayame | 32 | 7 | 7 | 1 | 40 | 470144 | 512 | 68 | 127491 | 18.54 | 0.599 | 56.0 | 0.000 |
| YCSB-C | Single WAL | 32 | 1 | 0 | 0 | 32 | 656152 | 48 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | P-WAL | 32 | 32 | 0 | 0 | 32 | 649359 | 49 | 0 | 0 | 0.00 | 1.000 | 0.0 | 0.000 |
| YCSB-C | Ayame | 32 | 7 | 7 | 1 | 40 | 650445 | 49 | 0 | 0 | 0.00 | 1.000 | 56.0 | 0.000 |
