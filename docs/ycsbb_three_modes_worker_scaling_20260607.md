# YCSB-B three-mode worker scaling

date: 2026-06-07T16:39:03

## Conditions

| item | value |
|---|---|
| workload | YCSB-B: 95% read / 5% update, 10 ops/tx |
| worker threads | 1,2,4,8,16,32 |
| repeats | 5 |
| seconds | 5 |
| logger_num | 8 |
| committer_num | 1 |
| parameter policy | mode-tuned |
| max_pending | 65536 |

Mode-tuned parameters are used here because the goal of this figure is to show a representative operating point where ack throughput is comparable, while dependency frontier avoids the global-prefix backlog/tail-latency problem. This is not the single-parameter fairness plot; it is the presentation figure requested for the three-mode behavior.

| mode | group_size | flush_us |
|---|---:|---:|
| worker-wait global prefix | 8 | 100 |
| async global LSN prefix | 4 | 0 |
| async dep frontier cstamp | 8 | 100 |

## PDF outputs

- ack tps: `paper/figures/fig_ycsbb_three_modes_ack_tps.pdf`
- p99: `paper/figures/fig_ycsbb_three_modes_p99.pdf`
- pending: `paper/figures/fig_ycsbb_three_modes_pending.pdf`

## Summary

| thread | mode | ack tps mean | ack tps stdev | p99 us | pending |
|---:|---|---:|---:|---:|---:|
| 1 | worker-wait global prefix | 5078 | 76.9 | 256 | 0 |
| 1 | async global LSN prefix | 50650 | 2066.8 | 1048576 | 65536 |
| 1 | async dep frontier cstamp | 72419 | 1257.1 | 256 | 10 |
| 2 | worker-wait global prefix | 10144 | 160.1 | 256 | 0 |
| 2 | async global LSN prefix | 86987 | 2067.5 | 524288 | 65535 |
| 2 | async dep frontier cstamp | 111546 | 2041.3 | 256 | 21 |
| 4 | worker-wait global prefix | 19839 | 203.5 | 256 | 0 |
| 4 | async global LSN prefix | 154669 | 7929.9 | 419430 | 65530 |
| 4 | async dep frontier cstamp | 179134 | 11072.7 | 307 | 33 |
| 8 | worker-wait global prefix | 37073 | 849.6 | 256 | 0 |
| 8 | async global LSN prefix | 232336 | 11257.2 | 262144 | 65534 |
| 8 | async dep frontier cstamp | 266936 | 24733.3 | 512 | 55 |
| 16 | worker-wait global prefix | 66937 | 501.4 | 256 | 0 |
| 16 | async global LSN prefix | 216547 | 3603.0 | 262144 | 65535 |
| 16 | async dep frontier cstamp | 249380 | 9864.3 | 512 | 65 |
| 32 | worker-wait global prefix | 109370 | 697.3 | 512 | 0 |
| 32 | async global LSN prefix | 217258 | 7815.0 | 262144 | 65554 |
| 32 | async dep frontier cstamp | 224947 | 3905.7 | 512 | 55 |
