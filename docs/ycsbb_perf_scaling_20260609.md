# YCSB-B perf stat worker scaling

date: 2026-06-16T16:49:48

`perf stat` was used to collect `task-clock` and `context-switches`.
CPU cores are computed as `task-clock-ms / elapsed-ms`.

## Conditions

| item | value |
|---|---|
| workload | YCSB-B |
| worker threads | 1,2,4,8,16,32,48,96 |
| systems | Single WAL, P-WAL, Ayame |
| seconds | 5 |
| repeats | 3 |
| Ayame group_size | 16 |
| Ayame flush_us | 50 |
| Ayame max_pending | 65536 |

summary csv: `paper/tables/ycsbb_perf_scaling_20260609.csv`
raw csv: `results/ycsbb_perf_scaling_20260616_164331/ycsbb_perf_scaling_raw_20260616_164331.csv`

## Figures

- `paper/figures/fig_ycsbb_perf_scaling_perf_stat.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_tps.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_cpu_cores.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_context_switches.pdf`

## Summary

| system | workers | ack tps | CPU cores | context switches |
|---|---:|---:|---:|---:|
| Single WAL | 1 | 22657 | 0.62 | 95483 |
| Single WAL | 2 | 20276 | 0.66 | 128195 |
| Single WAL | 4 | 27140 | 0.72 | 168328 |
| Single WAL | 8 | 26054 | 0.72 | 162677 |
| Single WAL | 16 | 23956 | 0.85 | 149875 |
| Single WAL | 32 | 18651 | 1.32 | 121014 |
| Single WAL | 48 | 23372 | 1.38 | 148678 |
| Single WAL | 96 | 21040 | 3.74 | 141485 |
| P-WAL | 1 | 19642 | 0.56 | 85291 |
| P-WAL | 2 | 32141 | 0.79 | 160722 |
| P-WAL | 4 | 79088 | 1.61 | 419058 |
| P-WAL | 8 | 115955 | 2.89 | 695746 |
| P-WAL | 16 | 137717 | 6.16 | 958547 |
| P-WAL | 32 | 134391 | 10.94 | 1081663 |
| P-WAL | 48 | 148311 | 17.41 | 1718180 |
| P-WAL | 96 | 152255 | 39.83 | 3973008 |
| Ayame | 1 | 87519 | 1.65 | 150081 |
| Ayame | 2 | 143862 | 2.80 | 172309 |
| Ayame | 4 | 265851 | 4.65 | 187902 |
| Ayame | 8 | 386277 | 8.72 | 493725 |
| Ayame | 16 | 393199 | 15.98 | 1184063 |
| Ayame | 32 | 398979 | 24.35 | 2821043 |
| Ayame | 48 | 432021 | 26.14 | 4619251 |
| Ayame | 96 | 374285 | 27.57 | 5473752 |
