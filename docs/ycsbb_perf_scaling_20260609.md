# YCSB-B perf stat worker scaling

date: 2026-06-17T20:11:21

`perf stat` was used to collect `task-clock` and `context-switches`.
CPU cores are computed as `task-clock-ms / elapsed-ms`.

## Conditions

| item | value |
|---|---|
| workload | YCSB-B |
| worker threads | 1,12,24,36,48,60,72,84,96 |
| systems | Single WAL, P-WAL, Ayame |
| seconds | 5 |
| repeats | 3 |
| Ayame group_size | 64 |
| Ayame flush_us | 50 |
| Ayame max_pending | 65536 |

summary csv: `paper/tables/ycsbb_perf_scaling_20260609.csv`
raw csv: `paper/tables/ycsbb_perf_scaling_20260609.csv`

## Figures

- `paper/figures/fig_ycsbb_perf_scaling_perf_stat.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_tps.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_cpu_cores.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_context_switches.pdf`

## Summary

| system | workers | ack tps | CPU cores | context switches |
|---|---:|---:|---:|---:|
| Single WAL | 1 | 23707 | 0.62 | 99510 |
| Single WAL | 12 | 25380 | 0.74 | 158541 |
| Single WAL | 24 | 24061 | 0.92 | 150133 |
| Single WAL | 36 | 24059 | 1.12 | 150714 |
| Single WAL | 48 | 24081 | 1.33 | 151116 |
| Single WAL | 60 | 23925 | 1.66 | 151064 |
| Single WAL | 72 | 23785 | 2.09 | 151283 |
| Single WAL | 84 | 23831 | 2.54 | 151921 |
| Single WAL | 96 | 23713 | 3.65 | 152021 |
| P-WAL | 1 | 23935 | 0.59 | 100587 |
| P-WAL | 12 | 133624 | 4.35 | 865410 |
| P-WAL | 24 | 148512 | 9.05 | 1123487 |
| P-WAL | 36 | 159095 | 13.60 | 1260324 |
| P-WAL | 48 | 162612 | 18.26 | 1296583 |
| P-WAL | 60 | 166271 | 23.01 | 1339000 |
| P-WAL | 72 | 167514 | 27.26 | 1349805 |
| P-WAL | 84 | 171707 | 32.15 | 1381592 |
| P-WAL | 96 | 172101 | 38.67 | 1442279 |
| Ayame | 1 | 91261 | 1.50 | 186150 |
| Ayame | 12 | 436669 | 12.58 | 937029 |
| Ayame | 24 | 458271 | 22.32 | 2131135 |
| Ayame | 36 | 476118 | 28.37 | 3716425 |
| Ayame | 48 | 451156 | 28.30 | 5161680 |
| Ayame | 60 | 419489 | 26.79 | 5868212 |
| Ayame | 72 | 408765 | 27.13 | 6177755 |
| Ayame | 84 | 394821 | 27.98 | 6239889 |
| Ayame | 96 | 396179 | 28.63 | 6093064 |
