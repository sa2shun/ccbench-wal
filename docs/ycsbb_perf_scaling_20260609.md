# YCSB-B perf stat worker scaling

date: 2026-06-09T15:52:42

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
raw csv: `results/ycsbb_perf_scaling_20260609_154803/ycsbb_perf_scaling_raw_20260609_154803.csv`

## Figures

- `paper/figures/fig_ycsbb_perf_scaling_perf_stat.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_tps.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_cpu_cores.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_context_switches.pdf`

## Summary

| system | workers | ack tps | CPU cores | context switches |
|---|---:|---:|---:|---:|
| Single WAL | 1 | 28928 | 0.46 | 97963 |
| Single WAL | 2 | 32632 | 0.57 | 162103 |
| Single WAL | 4 | 32505 | 0.58 | 161439 |
| Single WAL | 8 | 32616 | 0.61 | 161886 |
| Single WAL | 16 | 29497 | 0.75 | 146971 |
| Single WAL | 32 | 29042 | 1.01 | 145446 |
| P-WAL | 1 | 29342 | 0.45 | 99274 |
| P-WAL | 2 | 53867 | 0.87 | 210683 |
| P-WAL | 4 | 97719 | 1.78 | 413660 |
| P-WAL | 8 | 161836 | 3.70 | 729465 |
| P-WAL | 16 | 205701 | 8.48 | 1071911 |
| P-WAL | 32 | 247425 | 18.13 | 1462729 |
| TideWAL | 1 | 122408 | 1.59 | 186083 |
| TideWAL | 2 | 195146 | 2.91 | 208916 |
| TideWAL | 4 | 297996 | 5.50 | 182894 |
| TideWAL | 8 | 452103 | 10.61 | 494083 |
| TideWAL | 16 | 510386 | 19.72 | 1237843 |
| TideWAL | 32 | 570554 | 32.25 | 3197369 |
