# YCSB-A/B/C worker-thread Ayame comparison

date: 2026-06-18T14:53:48

This rerun uses worker threads on the x-axis. All three systems use the same `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` binary. Single WAL is selected by `CCBENCH_WAL_MODE=shared`, while P-WAL and Ayame use `CCBENCH_WAL_MODE=per_thread`.

Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. Ayame also applies the read-only SI fast path: once a transaction is known to be read-only, it skips durability frontier collection and uses the empty-frontier read-only ack path.

## Conditions

| item | value |
|---|---|
| workloads | ycsb_a,ycsb_b,ycsb_c |
| worker threads | 1,12,24,36,48,60,72,84,96 |
| repeats | 5 |
| seconds | 5 |
| Ayame logger mapping | {12: 3, 24: 5, 36: 7, 48: 9, 60: 12, 72: 14, 84: 17, 96: 19} |
| Ayame group_size | 256 |
| Ayame flush_us | 50 |
| Ayame max_pending | 65536 |

summary csv: `paper/tables/ycsbabc_ayame_worker_threads_20260609.csv`
raw csv: `results/ycsbabc_ayame_worker_threads_20260618_135841/ycsbabc_ayame_worker_threads_raw_20260618_135841.csv`

## Figures

- `paper/figures/fig_ycsbabc_worker_threads_throughput.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_latency.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_pending.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_ayame_speedup_vs_pwal.pdf`

## 32-worker summary

| workload | system | workers | WAL streams | flushers | committers | total active | ack tps | p99 us | pending | fdatasync count | commits/fdatasync | read-only ratio | frontier bytes/tx | WAL atomic/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
