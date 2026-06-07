# YCSB-B WAL/P-WAL/Cstamp-PWAL scaling

date: 2026-06-07T20:24:26

## Conditions

| item | value |
|---|---|
| workload | YCSB-B: 95% read / 5% update, 10 ops/tx |
| worker threads | 1,2,4,8,16,32 |
| repeats | 5 |
| seconds | 5 |
| async logger_num | 8 |
| async committer_num | 1 |
| async group_size | 8 |
| async flush_us | 100 |
| async max_pending | 65536 |

PDF outputs:
- ack tps: `paper/figures/fig_ycsbb_wal_pwal_cstamp_ack_tps.pdf`
- latency: `paper/figures/fig_ycsbb_wal_pwal_cstamp_latency.pdf`
- pending: `paper/figures/fig_ycsbb_wal_pwal_cstamp_pending.pdf`

Latency graph uses median across repeats. It uses measured `ack_latency_p99_us` when the mode exports it. Legacy Single WAL does not export ack latency buckets, so that line falls back to closed-loop average latency computed as `worker_threads / ack_tps`.

## Summary

| thread | mode | ack tps mean | ack tps stdev | latency us median | pending |
|---:|---|---:|---:|---:|---:|
| 1 | Single WAL | 13098 | 451.5 | 77 | 0 |
| 1 | P-WAL | 12742 | 172.3 | 128 | 0 |
| 1 | Async dep frontier cstamp | 75376 | 4480.6 | 512 | 14 |
| 2 | Single WAL | 12775 | 466.4 | 155 | 0 |
| 2 | P-WAL | 22217 | 804.9 | 128 | 0 |
| 2 | Async dep frontier cstamp | 110400 | 12849.1 | 256 | 18 |
| 4 | Single WAL | 12815 | 290.8 | 309 | 0 |
| 4 | P-WAL | 41511 | 1884.1 | 128 | 0 |
| 4 | Async dep frontier cstamp | 188529 | 16608.3 | 1024 | 32 |
| 8 | Single WAL | 12340 | 345.9 | 652 | 0 |
| 8 | P-WAL | 67299 | 3317.2 | 128 | 0 |
| 8 | Async dep frontier cstamp | 242850 | 17559.2 | 512 | 59 |
| 16 | Single WAL | 11951 | 167.8 | 1333 | 0 |
| 16 | P-WAL | 88130 | 1444.1 | 256 | 0 |
| 16 | Async dep frontier cstamp | 243182 | 9961.6 | 512 | 59 |
| 32 | Single WAL | 12023 | 96.2 | 2667 | 0 |
| 32 | P-WAL | 109029 | 1443.6 | 512 | 0 |
| 32 | Async dep frontier cstamp | 219897 | 7671.9 | 512 | 55 |
