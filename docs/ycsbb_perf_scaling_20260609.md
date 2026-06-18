# YCSB-B perf stat worker scaling

date: 2026-06-18T15:35:43

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
| Ayame group_size | 256 |
| Ayame flush_us | 50 |
| Ayame max_pending | 65536 |

summary csv: `paper/tables/ycsbb_perf_scaling_20260609.csv`
raw csv: `results/ycsbb_perf_scaling_20260618_152438/ycsbb_perf_scaling_raw_20260618_152438.csv`

## Figures

- `paper/figures/fig_ycsbb_perf_scaling_perf_stat.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_tps.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_cpu_cores.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_context_switches.pdf`

## Summary

| system | workers | ack tps | CPU cores | context switches |
|---|---:|---:|---:|---:|
| Single WAL | 1 | 22599 | 0.60 | 95725 |
| Single WAL | 12 | 25056 | 0.75 | 156268 |
| Single WAL | 24 | 24442 | 0.95 | 152215 |
| Single WAL | 36 | 24252 | 1.10 | 151757 |
| Single WAL | 48 | 23928 | 1.34 | 150637 |
| Single WAL | 60 | 23840 | 1.68 | 151265 |
| Single WAL | 72 | 24002 | 2.07 | 152261 |
| Single WAL | 84 | 23992 | 2.56 | 152605 |
| Single WAL | 96 | 23824 | 3.45 | 152533 |
| P-WAL | 1 | 23708 | 0.58 | 100324 |
| P-WAL | 12 | 126791 | 4.31 | 834426 |
| P-WAL | 24 | 151268 | 9.29 | 1135483 |
| P-WAL | 36 | 159648 | 13.69 | 1264451 |
| P-WAL | 48 | 162345 | 18.13 | 1300446 |
| P-WAL | 60 | 164884 | 22.73 | 1334969 |
| P-WAL | 72 | 167461 | 27.62 | 1356230 |
| P-WAL | 84 | 169555 | 32.27 | 1361165 |
| P-WAL | 96 | 170091 | 38.13 | 1453237 |
| Ayame | 1 | 97735 | 1.49 | 188414 |
| Ayame | 12 | 430776 | 12.58 | 906134 |
| Ayame | 24 | 454119 | 21.88 | 2249930 |
| Ayame | 36 | 467333 | 27.48 | 3763193 |
| Ayame | 48 | 428166 | 25.38 | 5367470 |
| Ayame | 60 | 425243 | 27.63 | 6032407 |
| Ayame | 72 | 418243 | 28.23 | 6000863 |
| Ayame | 84 | 401379 | 28.77 | 6108369 |
| Ayame | 96 | 380881 | 27.58 | 6013738 |
