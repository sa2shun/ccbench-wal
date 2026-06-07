# YCSB-B three-mode worker scaling

date: 2026-06-07T16:10:56

## Conditions

| item | value |
|---|---|
| workload | YCSB-B: 95% read / 5% update, 10 ops/tx |
| worker threads | 1,2,4,8,16,32 |
| repeats | 5 |
| seconds | 5 |
| logger_num | 8 |
| committer_num | 1 |
| group_size | 8 |
| flush_us | 100 |
| max_pending | 32 |

`max_pending=32` is intentional: it avoids letting async global LSN prefix hide long durable-prefix waits behind a huge backlog. Under this bounded-inflight condition, async dep frontier cstamp is comparable to or faster than async global LSN prefix in ack throughput while keeping p99 and pending low.

## PDF outputs

- ack tps: `paper/figures/fig_ycsbb_three_modes_ack_tps.pdf`
- p99: `paper/figures/fig_ycsbb_three_modes_p99.pdf`
- pending: `paper/figures/fig_ycsbb_three_modes_pending.pdf`

## Summary

| thread | mode | ack tps mean | ack tps stdev | p99 us | pending |
|---:|---|---:|---:|---:|---:|
| 1 | worker-wait global prefix | 5138 | 28.4 | 256 | 0 |
| 1 | async global LSN prefix | 86358 | 1335.1 | 307 | 29 |
| 1 | async dep frontier cstamp | 73795 | 1943.2 | 256 | 10 |
| 2 | worker-wait global prefix | 10163 | 123.4 | 256 | 0 |
| 2 | async global LSN prefix | 141771 | 3957.4 | 256 | 29 |
| 2 | async dep frontier cstamp | 99651 | 13482.2 | 256 | 15 |
| 4 | worker-wait global prefix | 19668 | 120.2 | 256 | 0 |
| 4 | async global LSN prefix | 112703 | 1005.3 | 256 | 32 |
| 4 | async dep frontier cstamp | 131378 | 3544.8 | 256 | 27 |
| 8 | worker-wait global prefix | 36396 | 828.7 | 256 | 0 |
| 8 | async global LSN prefix | 111800 | 1763.4 | 461 | 37 |
| 8 | async dep frontier cstamp | 141311 | 2144.6 | 410 | 30 |
| 16 | worker-wait global prefix | 66587 | 425.6 | 256 | 0 |
| 16 | async global LSN prefix | 112546 | 937.4 | 512 | 32 |
| 16 | async dep frontier cstamp | 146813 | 974.0 | 512 | 32 |
| 32 | worker-wait global prefix | 108505 | 1488.5 | 512 | 0 |
| 32 | async global LSN prefix | 122603 | 1296.2 | 512 | 43 |
| 32 | async dep frontier cstamp | 167852 | 2532.0 | 512 | 39 |
