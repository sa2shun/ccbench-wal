# YCSB-B Cstamp-PWAL plateau / latency analysis

date: 2026-06-07

## 目的

`async_dep_frontier_cstamp` が YCSB-B で worker threads=8 あたりから throughput が頭打ちになる理由と、p99 latency で plain P-WAL に負ける理由を counter と hyperparameter probe で分解した。

対象グラフ:

- `paper/figures/fig_ycsbb_wal_pwal_cstamp_ack_tps.pdf`
- `paper/figures/fig_ycsbb_wal_pwal_cstamp_latency.pdf`
- `paper/figures/fig_ycsbb_wal_pwal_cstamp_pending.pdf`

latency 図は今回、標準偏差の帯を消し、repeat 間の中央値を折れ線だけで描くように変更した。Single WAL は ack latency bucket を出していないため、closed-loop average latency を fallback として使う。P-WAL と Cstamp-PWAL は measured p99 durable-ack latency を使う。

## 条件

メインデータ:

| item | value |
|---|---|
| workload | YCSB-B: 95% read / 5% update, 10 ops/tx |
| worker threads | 1,2,4,8,16,32 |
| repeats | 5 |
| seconds | 5 |
| async mode | `async_dep_frontier_cstamp` |
| logger threads | 8 |
| committer threads | 1 |
| group_size | 8 |
| flush_us | 100 |
| max_pending | 65536 |

追加 probe:

- `results/cstamp_param_probe_20260607_202008/cstamp_param_probe_20260607_202008.csv`
- `results/cstamp_low_thread_latency_probe_20260607_202155/cstamp_low_thread_latency_probe_20260607_202155.csv`
- `results/cstamp_post_fastpath_probe_20260607_202753/post_fastpath_probe.csv`

## 結論

一言で言うと、8 worker 以降の頭打ちは storage sync ではなく、ERMIA version frontier metadata の collect / publish が支配的になっているためである。

`async_dep_frontier_cstamp` は 8 worker で 242K tx/s まで伸びるが、16 worker では 243K tx/s、32 worker では 220K tx/s で頭打ちになる。この間、`fdatasync_ns/tx` は約 18-20us でほぼ横ばいだが、`frontier_collect_ns/tx` と `frontier_publish_ns/tx` は大きく増える。

| worker | ack tps | p99 median us | pending | frontier collect ns/tx | frontier publish ns/tx | fdatasync ns/tx | waitlist reg ns/tx |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 242,850 | 512 | 59 | 3,845.8 | 15,362.9 | 18,291.1 | 3,460.3 |
| 16 | 243,182 | 512 | 59 | 13,838.4 | 37,931.8 | 17,954.7 | 7,847.3 |
| 32 | 219,897 | 512 | 55 | 49,847.1 | 99,366.3 | 19,547.4 | 8,339.2 |

したがって、今の plateau の主因は `fdatasync` ではない。YCSB-B は read-heavy なので 1 tx あたり約 9.5 個の read frontier update が発生する。この read frontier publish が 16/32 worker で cache coherence、shared_ptr allocation/refcount、CAS retry を増やし、worker 側の logical commit path を詰まらせている。

## なぜ P-WAL より latency が悪いか

plain P-WAL は throughput は低いが、durable ack の構造は単純である。

| worker | P-WAL ack tps | P-WAL p99 median us | Cstamp-PWAL ack tps | Cstamp-PWAL p99 median us |
|---:|---:|---:|---:|---:|
| 1 | 12,742 | 128 | 75,376 | 512 |
| 2 | 22,217 | 128 | 110,400 | 256 |
| 4 | 41,511 | 128 | 188,529 | 1,024 |
| 8 | 67,299 | 128 | 242,850 | 512 |
| 16 | 88,130 | 256 | 243,182 | 512 |
| 32 | 109,029 | 512 | 219,897 | 512 |

P-WAL は worker-wait 型で、各 transaction は基本的に自分の WAL write/fdatasync を待つだけで、dependency frontier の collect/publish、waitlist registration、committer queue wait を持たない。そのため peak throughput は低いが、queue を抱えない分、低スレッドでは p99 が小さく見える。

一方 `async_dep_frontier_cstamp` は worker を fdatasync wait から逃がすために async durable ack にしている。その代わり、client ack latency には以下が入る。

- group commit の batch wait
- flusher による write/fdatasync
- dependency frontier waitlist registration
- committer event processing
- read/write frontier collect
- read/write frontier publish

つまり `async_dep_frontier_cstamp` は throughput と backlog 制御のために pipeline を深くしているので、低スレッドの p99 latency だけを見ると P-WAL に負けやすい。32 worker では p99 median は P-WAL と同じ 512us まで近づき、throughput は Cstamp-PWAL が約 2.0x 高い。

## hyperparameter probe の結果

group size を小さくすると latency が下がるかを確認したが、逆に大きく悪化した。理由は fdatasync の batch が小さすぎて storage sync capacity を使い切れず、pending が膨らむためである。

代表値:

| worker | group_size | flush_us | ack tps | p99 us | pending | fdatasync ns/tx |
|---:|---:|---:|---:|---:|---:|---:|
| 8 | 1 | 0 | 56,858 | 1,048,576 | 65,540 | 120,693 |
| 8 | 2 | 0 | 97,479 | 524,288 | 65,537 | 60,173 |
| 8 | 4 | 0 | 162,204 | 131,072 | 22,244 | 36,463 |
| 8 | 8 | 0 | 187,132 | 512 | 40 | 27,682 |
| 8 | 8 | 100 | 190,378 | 512 | 78 | 18,137 |
| 16 | 4 | 0 | 170,412 | 131,072 | 23,729 | 33,591 |
| 16 | 8 | 0 | 212,046 | 1,024 | 60 | 23,994 |
| 16 | 8 | 100 | 187,125 | 512 | 66 | 19,143 |
| 32 | 4 | 0 | 147,303 | 524,288 | 64,902 | 34,928 |
| 32 | 8 | 0 | 183,926 | 512 | 56 | 26,756 |
| 32 | 8 | 100 | 165,098 | 512 | 64 | 20,730 |

読み方:

- `group_size=1,2,4` は p99 と pending が壊れる。
- `group_size=8` が latency / pending の安定点。
- `flush_us=0,50,100` は若干の trade-off を作るが、8 worker 以降の plateau は解消しない。
- logger を 4/16/32 に振っても根本改善はなかった。logger=4 は backlog が増えやすく、logger=16/32 は frontier vector と flusher overhead が増える。

したがって、hyperparameter で解ける問題ではなく、metadata path の実装コストが支配的である。

## 入れた軽量改善

`publishReadFrontier()` に fast path を追加した。

既存 `read_frontier` が今回 publish しようとしている `closed_frontier` を既に包含している場合は、new frontier の allocation と CAS を skip する。

これは dependency を削らないため安全である。ただし短い post-fix probe では、大きな性能改善は出なかった。

| worker | ack tps | p99 us | pending | frontier publish ns/tx |
|---:|---:|---:|---:|---:|
| 8 | 191K 前後 | 256-512 | 49-77 | 15.3-17.0us |
| 16 | 194K-202K | 512 | 50-78 | 36.0-39.3us |
| 32 | 178K-189K | 512 | 61-63 | 91.4-96.5us |

効きが小さい理由は、YCSB-B では各 transaction の `closed_frontier` が新しい local_seq を含むため、read version 側の既存 frontier が十分に進んでいるケースが少ないからである。

この fast path は no-regret optimization として残せるが、plateau を解く本命ではない。

## 本当に改善するなら何を変えるべきか

次の最適化候補は、hyperparameter ではなく frontier publish/collect の構造変更である。

1. read frontier publish を per-read-version で即時 shared_ptr/CAS しない

   現状は YCSB-B で 1 tx あたり約 9.5 回 `read_frontier_` を更新する。これが read-heavy workload で最大の負担になっている。epoch/batch で集約する、record 単位でまとめる、または read frontier update を遅延する設計が必要。

2. `shared_ptr<const WalFrontier>` を hot path から外す

   allocation と refcount が commit path に乗っている。logger_num が 8 なら frontier は 64 bytes 程度なので、inline small frontier、object pool、epoch-owned immutable buffer などに置き換える価値がある。

3. empty/single-shard frontier の fast path を作る

   dependency frontier が self log だけ、または nonzero entry が 1 個だけの transaction は waitlist registration を特殊化できる。現状は全 shard を scan して shared request を作る。

4. read frontier を本当に必要なケースに絞る

   現在の実装は correctness 優先で、read/write dependency を保守的に version frontier へ publish している。ERMIA/SSN metadata で削れる dependency を見つけられれば、YCSB-B の publish cost を大きく下げられる可能性が高い。

## 論文向けの書き方

現時点で正しく言えること:

- Cstamp-PWAL は P-WAL より常に低 latency ではない。
- Cstamp-PWAL は worker を fdatasync wait から外すことで throughput を大きく上げる。
- その代わり、現実装では read/write frontier metadata の publish/collect が 8 worker 以降の scalability limit になる。
- YCSB-B では P-WAL の 32 worker throughput が 109K tx/s に対し、Cstamp-PWAL は 220K tx/s まで出る。
- latency は median p99 で 32 worker では P-WAL と Cstamp-PWAL がどちらも 512us。ただし低スレッドでは P-WAL の方が小さい。

避けるべき主張:

- Cstamp-PWAL は常に P-WAL より latency が良い。
- group/flush tuning だけで 8 worker plateau を解ける。

使うべき主張:

> Cstamp-PWAL shifts the bottleneck from storage synchronization to dependency-frontier metadata maintenance. This is useful because it identifies the next optimization target: reducing read-frontier publication and frontier object management overhead.

日本語では、

> Cstamp-PWAL により fdatasync wait は worker path から外せたが、次のボトルネックは dependency frontier の collect/publish に移った。特に YCSB-B では read-heavy なため、read frontier update が commit path を支配する。したがって次の実装改善は WAL hyperparameter ではなく frontier metadata の軽量化である。
