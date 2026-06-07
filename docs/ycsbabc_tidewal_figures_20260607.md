# YCSB-A/B/C TideWAL figure set

This note records the additional paper figures generated for YCSB-A and YCSB-C, plus several supporting figures that make the workload sensitivity clearer.

## Conditions

| item | value |
|---|---|
| systems | Single WAL, P-WAL, TideWAL |
| worker threads | 1, 2, 4, 8, 16, 32 |
| YCSB-A | 50% read / 50% update, 10 ops/tx |
| YCSB-B | 95% read / 5% update, 10 ops/tx |
| YCSB-C | 100% read, 10 ops/tx |
| TideWAL parameters | logger=4, committer=1, group_size=16, flush_us=50, max_pending=65536 |
| repeats for new A/C runs | 3 |

YCSB-B uses the existing paper table. YCSB-A and YCSB-C were newly measured by `scripts/run_ycsbabc_tidewal_figures.py`.

## Main 32-worker values

| workload | system | ack tps | p99 us | pending |
|---|---|---:|---:|---:|
| YCSB-A | Single WAL | 10,231 | 3,127 | 0 |
| YCSB-A | P-WAL | 72,197 | 512 | 0 |
| YCSB-A | TideWAL | 260,663 | 32,768 | 7,240 |
| YCSB-B | Single WAL | 12,023 | 2,667 | 0 |
| YCSB-B | P-WAL | 109,029 | 512 | 0 |
| YCSB-B | TideWAL | 374,003 | 512 | 49 |
| YCSB-C | Single WAL | 12,140 | 2,634 | 0 |
| YCSB-C | P-WAL | 115,257 | 256 | 0 |
| YCSB-C | TideWAL | 757,343 | 128 | 0 |

## Generated figures

YCSB-A scaling:

- `paper/figures/fig_ycsba_tidewal_ack_tps.pdf`
- `paper/figures/fig_ycsba_tidewal_latency.pdf`
- `paper/figures/fig_ycsba_tidewal_pending.pdf`

YCSB-C scaling:

- `paper/figures/fig_ycsbc_tidewal_ack_tps.pdf`
- `paper/figures/fig_ycsbc_tidewal_latency.pdf`
- `paper/figures/fig_ycsbc_tidewal_pending.pdf`

Supporting figures:

- `paper/figures/fig_ycsbabc_32thread_throughput.pdf`
- `paper/figures/fig_ycsbabc_32thread_latency.pdf`
- `paper/figures/fig_ycsbabc_tidewal_speedup_vs_pwal.pdf`
- `paper/figures/fig_ycsbabc_tidewal_overhead_32thread.pdf`

## How to use these figures

The strongest main-paper addition is `fig_ycsbabc_32thread_throughput.pdf`: it shows that TideWAL's benefit depends on workload mix, with the largest gain on read-only YCSB-C and a large gain on read-heavy YCSB-B.

The next most useful supporting figure is `fig_ycsbabc_tidewal_overhead_32thread.pdf`: it explains why YCSB-A is less clean. YCSB-A has many read/write frontier publishes, so TideWAL gets high throughput but accumulates backlog and tail latency at high worker counts.

The YCSB-C scaling figure is useful to explain read-only behavior. Read-only transactions collect durability dependencies and wait before returning, but do not publish durability frontier metadata to future writers. Therefore TideWAL has pending=0 and no WAL fdatasync cost on YCSB-C.

The YCSB-A latency/pending figures should be used carefully. They are useful as a limitation/analysis result: dense update workloads still create dependency metadata cost and backlog. They should not be used to claim that TideWAL always improves tail latency.
