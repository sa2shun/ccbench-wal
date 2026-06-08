# TideWAL diagnostic effects

date: 2026-06-08T18:52:44

This is an internal diagnostic experiment, not the paper's main 3-system comparison.
The flush pipeline is fixed; only the target mechanism changes.

## 結論

Q1. cstamp と LSN の統合はどれくらい効くか？

- YCSB-A では cstamp 版が separate-LSN 版の 1.025x。
- YCSB-B では cstamp 版が separate-LSN 版の 1.013x。
- WAL-side global atomic/tx は、LSN 版では YCSB-A で 6.000、YCSB-B で 0.901。cstamp 版ではどちらも 0。
- WAL LSN allocation cost は、LSN 版では YCSB-A で 1056.6 ns/tx、YCSB-B で 154.5 ns/tx。cstamp 版では 0。
- つまり、cstamp 統合は WAL 側の separate logical LSN allocation を明確に消している。ただし、この I/O-light 条件でも throughput 効果は 1〜3% 程度なので、主張は「大幅な高速化」より「CC order と WAL/recovery order の二重管理削除」が妥当。

Q2. dependency frontier は global prefix wait をどれくらい削るか？

- straggler sleep が 0〜500us では、global prefix の方が ack tps は高い。ただし pending は global prefix が 450〜590、dep frontier が 90〜110 程度で、dep frontier の方が backlog は小さい。
- straggler sleep = 1000us では、global prefix は 432K tps、p99 131ms、pending 65,379、max durable lag 10,390 まで悪化する。
- 同じ 1000us straggler で dep frontier は 460K tps、p99 2ms、pending 92、max durable lag 37 に留まる。
- つまり、dependency frontier の効果は「通常時の peak throughput」ではなく、「slow WAL shard が出た時に unrelated transaction を global prefix に巻き込まず、backlog と tail latency を抑える」点に出る。

## Conditions

| item | value |
|---|---|
| worker threads | 32 |
| logger_num | 7 |
| committer_num | 1 |
| seconds | 5 |
| repeats | 5 |
| fdatasync | skip |
| group_size | 64 |
| flush_us | 1000 |
| max_pending | 65536 |
| read-only WAL skip | on |
| raw csv | `paper/tables/tidewal_diagnostic_effects_raw_20260608.csv` |
| summary csv | `paper/tables/tidewal_diagnostic_effects_summary_20260608.csv` |

## Figures

- `paper/figures/fig_diag_cstamp_integration_tps.pdf`
- `paper/figures/fig_diag_cstamp_integration_overhead.pdf`
- `paper/figures/fig_diag_frontier_straggler_tps.pdf`
- `paper/figures/fig_diag_frontier_straggler_latency_pending.pdf`

## Experiment 1: cstamp and LSN integration

Modes: `async_dep_frontier_lsn` vs `async_dep_frontier_cstamp`.
The pipeline and dependency frontier are the same; only the WAL-side logical LSN allocation differs.

| workload | mode | ack tps | p99 us | pending | WAL atomic/tx | LSN alloc ns/tx | cycles/tx | instr/tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| YCSB-A | async_dep_frontier_lsn | 304240 | 1024 | 160 | 6.000 | 1056.6 | 216987 | 140517 |
| YCSB-A | async_dep_frontier_cstamp | 311792 | 1024 | 155 | 0.000 | 0.0 | 214300 | 140596 |
| YCSB-B | async_dep_frontier_lsn | 459586 | 1024 | 96 | 0.901 | 154.5 | 150814 | 74692 |
| YCSB-B | async_dep_frontier_cstamp | 465378 | 1024 | 84 | 0.000 | 0.0 | 150541 | 75473 |

- YCSB-A: cstamp / separate-LSN throughput ratio = 1.025
- YCSB-B: cstamp / separate-LSN throughput ratio = 1.013

## Experiment 2: dependency frontier vs global prefix

Modes: `async_global_lsn_prefix` vs `async_dep_frontier_lsn`.
The pipeline and separate WAL LSN are the same; only the durable ack condition differs.

| straggler sleep us | mode | ack tps | p99 us | pending | durable lag max | dep wait cond/tx | ctx switches |
|---:|---|---:|---:|---:|---:|---:|---:|
| 0 | async_global_lsn_prefix | 545815 | 1024 | 483 | 71.2 | 0.000 | 2926735 |
| 0 | async_dep_frontier_lsn | 462479 | 1024 | 93 | 22.4 | 0.413 | 3552728 |
| 100 | async_global_lsn_prefix | 541390 | 1024 | 453 | 65.8 | 0.000 | 2906504 |
| 100 | async_dep_frontier_lsn | 453517 | 1024 | 106 | 23.2 | 0.414 | 3581924 |
| 500 | async_global_lsn_prefix | 584141 | 1024 | 591 | 89.2 | 0.000 | 2909453 |
| 500 | async_dep_frontier_lsn | 457618 | 2048 | 109 | 36.4 | 0.416 | 3539735 |
| 1000 | async_global_lsn_prefix | 432092 | 131072 | 65379 | 10390.2 | 0.000 | 2113012 |
| 1000 | async_dep_frontier_lsn | 459936 | 2048 | 92 | 36.6 | 0.419 | 3482300 |

- straggler_sleep=0us: dep-frontier / global-prefix throughput ratio = 0.847
- straggler_sleep=100us: dep-frontier / global-prefix throughput ratio = 0.838
- straggler_sleep=500us: dep-frontier / global-prefix throughput ratio = 0.783
- straggler_sleep=1000us: dep-frontier / global-prefix throughput ratio = 1.064

## Interpretation

- cstamp integration is isolated by comparing dep-frontier LSN and dep-frontier cstamp under identical async pipeline settings.
- dependency frontier is isolated by comparing global-prefix and dep-frontier LSN under identical async pipeline and separate-LSN settings.
- Because fdatasync is skipped, these runs emphasize ordering, queueing, and dependency-wait costs rather than storage latency.
