# YCSB-B Cstamp-PWAL hyperparameter sweep

date: 2026-06-07

## 目的

`async_dep_frontier_cstamp` について、以下の hyperparameter を振り、plain P-WAL に常に throughput で勝てるか、また 8 worker 以降の頭打ちを避けられるかを確認した。

| parameter | values |
|---|---|
| logger_num | 4, 8, 16 |
| committer_num | 1, 2 |
| group_size | 4, 8, 16 |
| flush_us | 0, 50, 100, 200 |

探索 sweep は worker threads = 8, 16, 32 で全 72 config を `seconds=2, repeat=1` で実行した。その後、有望な 3 config を worker threads = 1, 2, 4, 8, 16, 32 で `seconds=3, repeat=3` により再測定した。

## 生成物

| artifact | path |
|---|---|
| selected summary CSV | `paper/tables/ycsbb_cstamp_selected_hyperparams_20260607.csv` |
| throughput PDF | `paper/figures/fig_ycsbb_cstamp_tuned_vs_pwal_ack_tps.pdf` |
| latency PDF | `paper/figures/fig_ycsbb_cstamp_tuned_vs_pwal_latency.pdf` |
| pending PDF | `paper/figures/fig_ycsbb_cstamp_tuned_vs_pwal_pending.pdf` |

latency 図は帯なしの折れ線のみである。

## 結論

一言で言うと、

> tuned Cstamp-PWAL は YCSB-B の 1-32 worker 全点で P-WAL より高 throughput を出せる。ただし 8/16 worker 以降で完全に scaling し続ける設定はこの範囲では見つからない。

最もバランスが良かった config はこれである。

| logger_num | committer_num | group_size | flush_us |
|---:|---:|---:|---:|
| 4 | 1 | 16 | 50 |

この config は 1-32 worker の全点で P-WAL に throughput 勝ちする。特に 32 worker では P-WAL の 109K tx/s に対して 177K tx/s で、約 1.62x である。

一方で、latency は常勝ではない。1-8 worker では P-WAL の p99=128us に対して tuned Cstamp-PWAL は p99=256us なので負ける。16 worker では同等、32 worker では Cstamp-PWAL の方が良い。

## tuned config vs P-WAL

P-WAL baseline は既存の 5-repeat 結果を使用した。Cstamp-PWAL は selected config の 3-repeat 平均である。

| worker | P-WAL tps | tuned Cstamp-PWAL tps | ratio | P-WAL p99 us | tuned Cstamp p99 us | pending |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 12,742 | 64,465 | 5.06x | 128 | 256 | 12 |
| 2 | 22,217 | 91,501 | 4.12x | 128 | 256 | 17 |
| 4 | 41,511 | 138,642 | 3.34x | 128 | 256 | 26 |
| 8 | 67,299 | 181,625 | 2.70x | 128 | 256 | 32 |
| 16 | 88,130 | 196,097 | 2.23x | 256 | 256 | 54 |
| 32 | 109,029 | 176,547 | 1.62x | 512 | 256 | 52 |

読み方:

- throughput は全 worker 数で P-WAL に勝つ。
- p99 latency は 1-8 worker では P-WAL に負ける。
- 16 worker では latency 同等。
- 32 worker では tuned Cstamp-PWAL の方が latency は良い。
- pending は 12-54 程度で安定しており、async global prefix のような巨大 backlog は出ていない。

## 頭打ちについて

tuned config の scaling は以下である。

| worker | tuned Cstamp-PWAL tps |
|---:|---:|
| 1 | 64,465 |
| 2 | 91,501 |
| 4 | 138,642 |
| 8 | 181,625 |
| 16 | 196,097 |
| 32 | 176,547 |

8 worker から 16 worker では少し伸びるが、32 worker では落ちる。したがって、今回の hyperparameter 範囲では「頭打ちしない」とは言えない。

ただし、以前の default config よりはかなり安定した。default は 8 worker で 242K、16 worker で 243K、32 worker で 220K と高い一方で、repeat によって p99 がぶれる。tuned config は peak tps は落ちるが、p99=256us と pending 50 前後で安定する。

## 8/16/32 worker での探索結果

安定条件を `p99 <= 1024us` かつ `pending <= 1000` として、各 worker の best は以下。

| worker | best config `(logger, committer, group, flush)` | ack tps | p99 us | pending |
|---:|---|---:|---:|---:|
| 8 | `(8, 1, 16, 0)` | 258,354 | 256 | 69 |
| 16 | `(16, 2, 16, 0)` | 211,604 | 512 | 49 |
| 32 | `(16, 2, 4, 50)` | 201,060 | 512 | 83 |

thread ごとに config を変えれば、32 worker でも 201K tx/s までは出る。しかし 8 worker の best 258K から見ると伸びていないため、根本的な scaling 問題は残る。

同一 config で 8/16/32 を一番フラットに通せたのは以下。

| config `(logger, committer, group, flush)` | 8 worker | 16 worker | 32 worker | 32/8 | p99 |
|---|---:|---:|---:|---:|---|
| `(4, 1, 16, 50)` | 192,775 | 203,861 | 189,532 | 0.983 | 256,256,256 |
| `(4, 1, 16, 100)` | 211,820 | 192,877 | 186,188 | 0.879 | 256,512,512 |
| `(16, 2, 8, 100)` | 231,958 | 196,079 | 200,708 | 0.865 | 256,512,512 |

このため、論文用に「安定 operating point」として使うなら `(4,1,16,50)` が一番扱いやすい。

## hyperparameter の読み方

### group_size

`group_size=4` は危険である。8 worker ではまだ動く config もあるが、16/32 worker では pending が数万から 65K まで膨らむ config が多い。

`group_size=8` と `group_size=16` は安定しやすい。latency と pending を重視するなら `group_size=16` が最も扱いやすい。

### logger_num

`logger_num=4` は 32 worker で best throughput は出にくいが、frontier vector が小さく、同一 config で 8/16/32 を通したときに安定しやすい。

`logger_num=8` は 8 worker で peak throughput が高い。

`logger_num=16` は 32 worker の best config には入るが、frontier vector と flusher overhead が増えるため、全体として常に良いわけではない。

### committer_num

`committer_num=2` は一部 config で効くが、全体を支配してはいない。今回の stable config は `committer_num=1` で十分だった。

これは現在のボトルネックが committer thread ではなく、worker-side の frontier collect/publish にあるためである。

### flush_us

`flush_us=0,50,100,200` の差はあるが、plateau を解くほどの差ではない。`flush_us=50` は stable config でよく、latency/pending と throughput のバランスが良い。

## なぜ頭打ちは残るか

selected config の counter は以下。

| worker | collect ns/tx | publish ns/tx | fdatasync ns/tx |
|---:|---:|---:|---:|
| 1 | 811 | 4,017 | 8,170 |
| 2 | 1,470 | 6,935 | 11,229 |
| 4 | 2,211 | 9,766 | 15,087 |
| 8 | 4,376 | 16,764 | 11,240 |
| 16 | 12,977 | 37,095 | 10,689 |
| 32 | 48,587 | 100,064 | 11,525 |

`fdatasync_ns/tx` は 8/16/32 worker で 10-12us 程度に収まっている。一方で `frontier_publish_ns/tx` は 8 worker で 16.8us、16 worker で 37.1us、32 worker で 100.1us まで増えている。

つまり、今の plateau は storage I/O ではなく、read/write frontier metadata の publish/collect によって起きている。

YCSB-B は 95% read なので、1 transaction あたり read frontier update が約 9.5 回発生する。これが 32 worker で shared_ptr allocation/refcount、atomic load/store、CAS、cache coherence を増やしている。

## 最終判断

今回の sweep で言えること:

1. P-WAL に throughput で常に勝つ config はある。
2. その best stable config は `logger_num=4, committer_num=1, group_size=16, flush_us=50`。
3. ただし latency は 1-8 worker では P-WAL に負ける。
4. 16 worker では latency 同等、32 worker では Cstamp-PWAL が勝つ。
5. 8/16/32 worker で完全に伸び続ける config は見つからなかった。
6. 頭打ちの主因は WAL hyperparameter ではなく `frontier_publish_ns/tx` と `frontier_collect_ns/tx` の増加である。

したがって、次に本当に必要なのは group/logger/flush の追加 sweep ではなく、frontier metadata path の実装改善である。具体的には、read frontier publish の batching、shared_ptr を使わない inline/object-pool frontier、single-shard frontier fast path が必要になる。
