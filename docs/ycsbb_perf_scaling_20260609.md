# YCSB-B perf stat worker scaling

date: 2026-06-13T17:37:35

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
raw csv: `results/ycsbb_perf_scaling_20260613_173257/ycsbb_perf_scaling_raw_20260613_173257.csv`

## Figures

- `paper/figures/fig_ycsbb_perf_scaling_perf_stat.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_tps.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_cpu_cores.pdf`
- `paper/figures/fig_ycsbb_perf_scaling_context_switches.pdf`

## Summary

| system | workers | ack tps | CPU cores | context switches |
|---|---:|---:|---:|---:|
| Single WAL | 1 | 23606 | 0.60 | 99173 |
| Single WAL | 2 | 27678 | 0.64 | 171495 |
| Single WAL | 4 | 26953 | 0.64 | 166854 |
| Single WAL | 8 | 27184 | 0.68 | 169416 |
| Single WAL | 16 | 24294 | 0.82 | 150998 |
| Single WAL | 32 | 24527 | 1.01 | 152950 |
| P-WAL | 1 | 24388 | 0.54 | 102351 |
| P-WAL | 2 | 44193 | 0.89 | 215631 |
| P-WAL | 4 | 78930 | 1.64 | 419543 |
| P-WAL | 8 | 124633 | 3.12 | 727773 |
| P-WAL | 16 | 163130 | 6.87 | 1090971 |
| P-WAL | 32 | 194634 | 14.38 | 1464114 |
| Ayame | 1 | 95804 | 1.43 | 191936 |
| Ayame | 2 | 139484 | 2.49 | 202422 |
| Ayame | 4 | 244655 | 4.57 | 189397 |
| Ayame | 8 | 353118 | 8.61 | 517611 |
| Ayame | 16 | 407646 | 15.97 | 1331543 |
| Ayame | 32 | 463621 | 25.77 | 3495385 |
