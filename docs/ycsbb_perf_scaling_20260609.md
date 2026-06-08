# YCSB-B perf stat worker scaling

date: 2026-06-09T07:48:05

`perf stat` was used to collect `task-clock` and `context-switches`.
CPU cores are computed as `task-clock-ms / elapsed-ms`.

## Conditions

| item | value |
|---|---|
| workload | YCSB-B |
| worker threads | 1,2,4,8,16,32 |
| systems | Single WAL, P-WAL, TideWAL |
| seconds | 5 |
| repeats | 3 |
| TideWAL group_size | 16 |
| TideWAL flush_us | 50 |
| TideWAL max_pending | 65536 |

summary csv: `paper/tables/ycsbb_perf_scaling_20260609.csv`
raw csv: `results/ycsbb_perf_scaling_20260609_074240/ycsbb_perf_scaling_raw_20260609_074240.csv`

## Figures

- `paper/figures/fig_ycsbb_perf_scaling_perf_stat.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_tps.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_cpu_cores.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_context_switches.pdf`

## Summary

| system | workers | ack tps | CPU cores | context switches |
|---|---:|---:|---:|---:|
| Single WAL | 1 | 29647 | 0.45 | 99888 |
| Single WAL | 2 | 32594 | 0.57 | 162061 |
| Single WAL | 4 | 32385 | 0.57 | 160970 |
| Single WAL | 8 | 32378 | 0.60 | 161202 |
| Single WAL | 16 | 29025 | 0.74 | 145076 |
| Single WAL | 32 | 29080 | 1.01 | 145873 |
| P-WAL | 1 | 29633 | 0.45 | 100090 |
| P-WAL | 2 | 54428 | 0.86 | 208459 |
| P-WAL | 4 | 98113 | 1.75 | 413758 |
| P-WAL | 8 | 163214 | 3.77 | 734901 |
| P-WAL | 16 | 208444 | 8.71 | 1073575 |
| P-WAL | 32 | 244122 | 17.92 | 1448989 |
| TideWAL | 1 | 110552 | 1.57 | 184644 |
| TideWAL | 2 | 172701 | 2.87 | 214741 |
| TideWAL | 4 | 273648 | 5.39 | 270767 |
| TideWAL | 8 | 388868 | 10.26 | 691645 |
| TideWAL | 16 | 413587 | 17.89 | 1929473 |
| TideWAL | 32 | 469655 | 29.48 | 4043411 |
