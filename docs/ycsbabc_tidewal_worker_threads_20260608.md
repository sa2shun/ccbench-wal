# YCSB-A/B/C worker-thread TideWAL comparison

date: 2026-06-08T15:10:16

This rerun uses worker threads on the x-axis. All three systems use the same `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` binary. Single WAL is selected by `CCBENCH_WAL_MODE=shared`, while P-WAL and TideWAL use `CCBENCH_WAL_MODE=per_thread`.

Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. TideWAL additionally has an empty-frontier read-only fast path: if a read-only transaction has no durable dependency, it does not enter the dependency waitlist.

## Conditions

| item | value |
|---|---|
| workloads | ycsb_a,ycsb_b,ycsb_c |
| worker threads | 1,2,4,8,16,32 |
| repeats | 3 |
| seconds | 5 |
| TideWAL logger mapping | {1: 1, 2: 1, 4: 1, 8: 2, 16: 4, 32: 7, 48: 9, 96: 19} |
| TideWAL group_size | 16 |
| TideWAL flush_us | 50 |
| TideWAL max_pending | 65536 |

summary csv: `paper/tables/ycsbabc_tidewal_worker_threads_20260608.csv`
raw csv: `results/ycsbabc_tidewal_worker_threads_20260608_145631/ycsbabc_tidewal_worker_threads_raw_20260608_145631.csv`

## Figures

- `paper/figures/fig_ycsbabc_worker_threads_throughput.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_latency.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_pending.pdf`
- `paper/figures/fig_ycsbabc_worker_threads_tidewal_speedup_vs_pwal.pdf`

## 32-worker summary

| workload | system | worker | logger | committer | total active | ack tps | p99 us | pending | read-only tx | read-only fast path | frontier collect ns/tx | waitlist reg ns/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 32 | 0 | 0 | 32 | 10544 | 4096 | 0 | 0.001 | 0.000 | 0.0 | 0.0 |
| YCSB-A | P-WAL | 32 | 32 | 0 | 32 | 69180 | 512 | 0 | 0.001 | 0.000 | 0.0 | 0.0 |
| YCSB-A | TideWAL | 32 | 7 | 1 | 40 | 307996 | 1024 | 120 | 0.001 | 0.000 | 27703.9 | 27783.1 |
| YCSB-B | Single WAL | 32 | 0 | 0 | 32 | 29434 | 2048 | 0 | 0.598 | 0.000 | 0.0 | 0.0 |
| YCSB-B | P-WAL | 32 | 32 | 0 | 32 | 244482 | 256 | 0 | 0.599 | 0.000 | 0.0 | 0.0 |
| YCSB-B | TideWAL | 32 | 7 | 1 | 40 | 456446 | 512 | 41 | 0.599 | 0.007 | 14181.4 | 27666.8 |
| YCSB-C | Single WAL | 32 | 0 | 0 | 32 | 802237 | 39 | 0 | 1.000 | 0.000 | 0.0 | 0.0 |
| YCSB-C | P-WAL | 32 | 32 | 0 | 32 | 794168 | 40 | 0 | 1.000 | 0.000 | 0.0 | 0.0 |
| YCSB-C | TideWAL | 32 | 7 | 1 | 40 | 783114 | 41 | 0 | 1.000 | 1.000 | 2681.7 | 0.0 |
