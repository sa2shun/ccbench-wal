# YCSB-B perf stat worker scaling

date: 2026-06-13T15:01:56

`perf stat` was used to collect `task-clock` and `context-switches`.
CPU cores are computed as `task-clock-ms / elapsed-ms`.

## Conditions

| item | value |
|---|---|
| workload | YCSB-B |
| worker threads | 1,2,4,8,16,32 |
| systems | Single WAL, P-WAL, Ayame |
| seconds | 5 |
| repeats | 3 |
| Ayame group_size | 16 |
| Ayame flush_us | 50 |
| Ayame max_pending | 65536 |

summary csv: `paper/tables/ycsbb_perf_scaling_20260609.csv`
raw csv: `results/ycsbb_perf_scaling_20260611_212006/ycsbb_perf_scaling_raw_20260611_212006.csv`

## Figures

- `paper/figures/fig_ycsbb_perf_scaling_perf_stat.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_tps.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_cpu_cores.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_context_switches.pdf`

## Summary

| system | workers | ack tps | CPU cores | context switches |
|---|---:|---:|---:|---:|
| Single WAL | 1 | 27942 | 0.46 | 94694 |
| Single WAL | 2 | 32264 | 0.58 | 160320 |
| Single WAL | 4 | 32051 | 0.58 | 159699 |
| Single WAL | 8 | 32048 | 0.60 | 159757 |
| Single WAL | 16 | 28915 | 0.75 | 144492 |
| Single WAL | 32 | 29051 | 1.00 | 145715 |
| P-WAL | 1 | 28835 | 0.45 | 97439 |
| P-WAL | 2 | 53738 | 0.88 | 209558 |
| P-WAL | 4 | 94214 | 1.79 | 398584 |
| P-WAL | 8 | 160428 | 3.84 | 727988 |
| P-WAL | 16 | 208906 | 8.72 | 1078449 |
| P-WAL | 32 | 245453 | 17.99 | 1448247 |
| Ayame | 1 | 121326 | 1.59 | 185827 |
| Ayame | 2 | 198496 | 2.92 | 198256 |
| Ayame | 4 | 324530 | 5.51 | 182321 |
| Ayame | 8 | 434886 | 10.53 | 528245 |
| Ayame | 16 | 505032 | 19.72 | 1267782 |
| Ayame | 32 | 573000 | 32.06 | 3283394 |
