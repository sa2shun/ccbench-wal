# YCSB-A/B/C fair total-thread TideWAL comparison

date: 2026-06-07T23:17:06

This rerun uses `total active threads = worker + flusher/logger + committer` for TideWAL. Single WAL and P-WAL use no background durability threads, so their worker count equals total active threads.

Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. For Single WAL and P-WAL this skips WAL records for read-only transactions. TideWAL keeps its read-only dependency wait semantics, but still skips WAL append and frontier publish for read-only transactions.

## Conditions

| item | value |
|---|---|
| workloads | ycsb_a,ycsb_b,ycsb_c |
| total active threads | 1,2,4,8,16,32 |
| repeats | 3 |
| seconds | 5 |
| TideWAL group_size | 16 |
| TideWAL flush_us | 50 |
| TideWAL max_pending | 65536 |

raw csv: `results/ycsbabc_tidewal_fair_total_20260607_230454/ycsbabc_tidewal_fair_total_raw_20260607_230454.csv`
summary csv: `paper/tables/ycsbabc_tidewal_fair_total_20260607.csv`

## Figures

- `paper/figures/fig_ycsbabc_fair_total_throughput.pdf`
- `paper/figures/fig_ycsbabc_fair_total_latency.pdf`
- `paper/figures/fig_ycsbabc_fair_total_pending.pdf`
- `paper/figures/fig_ycsbabc_fair_tidewal_speedup_vs_pwal.pdf`

## 32 total-thread summary

| workload | system | worker | logger | committer | ack tps | p99 us | pending | read-only tx ratio |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | Single WAL | 32 | 0 | 0 | 10119 | 4096 | 0 | 0.001 |
| YCSB-A | P-WAL | 32 | 32 | 0 | 71582 | 512 | 0 | 0.001 |
| YCSB-A | TideWAL | 24 | 7 | 1 | 297147 | 8192 | 508 | 0.001 |
| YCSB-B | Single WAL | 32 | 0 | 0 | 28516 | 2048 | 0 | 0.598 |
| YCSB-B | P-WAL | 32 | 32 | 0 | 253063 | 256 | 0 | 0.599 |
| YCSB-B | TideWAL | 24 | 7 | 1 | 461289 | 512 | 43 | 0.599 |
| YCSB-C | Single WAL | 32 | 0 | 0 | 793706 | 40 | 0 | 1.000 |
| YCSB-C | P-WAL | 32 | 32 | 0 | 1443722 | 22 | 0 | 1.000 |
| YCSB-C | TideWAL | 24 | 7 | 1 | 723000 | 64 | 0 | 1.000 |
