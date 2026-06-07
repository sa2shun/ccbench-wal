# ERMIA Cstamp-PWAL total-thread-budget report

date: 2026-06-07

## 結論

論文の main performance として使うべき total-thread-budget 比較を実装し、YCSB-B を中心に測定した。

一言で言うと、Cstamp-PWAL の現時点の勝ち筋は peak throughput 最大化ではなく、global LSN prefix が作る巨大な durable-ack backlog と tail latency を dependency frontier で抑えること。

total=48 の YCSB-B では、async global LSN prefix は 354K tx/s 出るが、pending は 65,498、p99 durable ack latency は 131ms まで悪化する。一方、async dep frontier cstamp は 209K tx/s と throughput は低いが、pending は 49、p99 は 512us に抑えられる。

## 実装したこと

`thread_num` を transaction worker 数として扱い、total active threads を次で定義した。

```text
total active threads = worker threads + flusher/logger threads + committer threads
```

async 系は以下の配分で測定した。

| total | worker | flusher/logger | committer |
|---:|---:|---:|---:|
| 4 | 2 | 1 | 1 |
| 8 | 5 | 2 | 1 |
| 16 | 12 | 3 | 1 |
| 24 | 18 | 5 | 1 |
| 32 | 24 | 7 | 1 |
| 48 | 38 | 9 | 1 |
| 96 | 76 | 19 | 1 |

pinning も入れた。`CCBENCH_CPU_LIST` を追加し、worker は CPU list の先頭から、flusher は worker の後ろ、committer は flusher の後ろに pin する。

CPU policy は以下。

| total | CPU policy |
|---:|---|
| 1,2,4,8,16,24 | socket0 physical cores |
| 32,48 | all physical cores, one logical CPU per core |
| 96 | all logical CPUs including SMT |

測定は `numactl --interleave=all` も併用した。

## 生成物

Main YCSB-B scaling:

- CSV: [ermia_cstamp_pwal_total_budget_20260607_095421_116549.csv](../results/ermia_cstamp_pwal_20260607_095421_116549/total_budget/ermia_cstamp_pwal_total_budget_20260607_095421_116549.csv)
- Report: [ermia_cstamp_pwal_total_budget_20260607_095421_116549.md](../results/ermia_cstamp_pwal_20260607_095421_116549/total_budget/ermia_cstamp_pwal_total_budget_20260607_095421_116549.md)
- Throughput graph: ![YCSB-B throughput](../results/ermia_cstamp_pwal_20260607_095421_116549/total_budget/ermia_cstamp_pwal_total_budget_20260607_095421_116549_throughput.svg)
- p99 graph: ![YCSB-B p99](../results/ermia_cstamp_pwal_20260607_095421_116549/total_budget/ermia_cstamp_pwal_total_budget_20260607_095421_116549_p99.svg)
- Pending graph: ![YCSB-B pending](../results/ermia_cstamp_pwal_20260607_095421_116549/total_budget/ermia_cstamp_pwal_total_budget_20260607_095421_116549_pending.svg)

Max-pending sweep:

- Report: [ermia_cstamp_pwal_max_pending_20260607_100737_898961.md](../results/ermia_cstamp_pwal_20260607_100737_898961/max_pending/ermia_cstamp_pwal_max_pending_20260607_100737_898961.md)
- Pareto graph: ![max pending pareto](../results/ermia_cstamp_pwal_20260607_100737_898961/max_pending/ermia_cstamp_pwal_max_pending_20260607_100737_898961_pareto.svg)

Cstamp vs separate LSN:

- Report: [ermia_cstamp_pwal_cstamp_20260607_101135_857816.md](../results/ermia_cstamp_pwal_20260607_101135_857816/cstamp/ermia_cstamp_pwal_cstamp_20260607_101135_857816.md)

YCSB-A representative point:

- Report: [ermia_cstamp_pwal_total_budget_20260607_101326_062322.md](../results/ermia_cstamp_pwal_20260607_101326_062322/total_budget/ermia_cstamp_pwal_total_budget_20260607_101326_062322.md)

## Main result: YCSB-B total-budget scaling

workload: YCSB-B, 95% read / 5% update, 10 ops/tx

| total | mode | worker | flusher | committer | ack tps | p99 us | pending | fdatasync/s | atomic/tx | frontier bytes/tx |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | no durability | 1 | 0 | 0 | 327,991 | 0 | 0 | 0 | 0.000 | 0 |
| 2 | no durability | 2 | 0 | 0 | 575,551 | 0 | 0 | 0 | 0.000 | 0 |
| 4 | no durability | 4 | 0 | 0 | 1,012,901 | 0 | 0 | 0 | 0.000 | 0 |
| 4 | worker-wait global prefix | 2 | 1 | 1 | 10,141 | 256 | 0 | 5,071 | 1.496 | 0 |
| 4 | async global LSN prefix | 2 | 1 | 1 | 88,559 | 524,288 | 65,535 | 13,118 | 1.500 | 0 |
| 4 | async dep frontier cstamp | 2 | 1 | 1 | 89,174 | 524,288 | 63,025 | 13,116 | 0.000 | 8 |
| 8 | no durability | 8 | 0 | 0 | 1,791,380 | 0 | 0 | 0 | 0.000 | 0 |
| 8 | worker-wait global prefix | 5 | 2 | 1 | 24,616 | 256 | 0 | 9,863 | 1.500 | 0 |
| 8 | async global LSN prefix | 5 | 2 | 1 | 129,971 | 524,288 | 65,527 | 18,439 | 1.500 | 0 |
| 8 | async dep frontier cstamp | 5 | 2 | 1 | 131,257 | 524,288 | 65,533 | 18,559 | 0.000 | 16 |
| 16 | no durability | 16 | 0 | 0 | 2,844,026 | 0 | 0 | 0 | 0.000 | 0 |
| 16 | worker-wait global prefix | 12 | 3 | 1 | 54,351 | 256 | 0 | 14,120 | 1.501 | 0 |
| 16 | async global LSN prefix | 12 | 3 | 1 | 191,162 | 262,144 | 65,531 | 25,944 | 1.501 | 0 |
| 16 | async dep frontier cstamp | 12 | 3 | 1 | 196,824 | 235,930 | 59,207 | 26,455 | 0.000 | 24 |
| 24 | no durability | 24 | 0 | 0 | 3,445,190 | 0 | 0 | 0 | 0.000 | 0 |
| 24 | worker-wait global prefix | 18 | 5 | 1 | 72,408 | 256 | 0 | 22,634 | 1.500 | 0 |
| 24 | async global LSN prefix | 18 | 5 | 1 | 254,787 | 262,144 | 65,522 | 34,564 | 1.500 | 0 |
| 24 | async dep frontier cstamp | 18 | 5 | 1 | 234,823 | 53,862 | 10,578 | 30,229 | 0.000 | 40 |
| 32 | no durability | 32 | 0 | 0 | 4,106,446 | 0 | 0 | 0 | 0.000 | 0 |
| 32 | worker-wait global prefix | 24 | 7 | 1 | 88,546 | 307 | 0 | 30,866 | 1.500 | 0 |
| 32 | async global LSN prefix | 24 | 7 | 1 | 303,654 | 262,144 | 65,527 | 41,526 | 1.500 | 0 |
| 32 | async dep frontier cstamp | 24 | 7 | 1 | 233,782 | 512 | 58 | 33,287 | 0.000 | 56 |
| 48 | no durability | 48 | 0 | 0 | 5,407,325 | 0 | 0 | 0 | 0.000 | 0 |
| 48 | worker-wait global prefix | 38 | 9 | 1 | 122,725 | 512 | 0 | 38,966 | 1.500 | 0 |
| 48 | async global LSN prefix | 38 | 9 | 1 | 354,433 | 131,072 | 65,498 | 48,200 | 1.500 | 0 |
| 48 | async dep frontier cstamp | 38 | 9 | 1 | 208,827 | 512 | 49 | 39,212 | 0.000 | 72 |
| 96 | no durability | 96 | 0 | 0 | 1,314,004 | 0 | 0 | 0 | 0.000 | 0 |
| 96 | worker-wait global prefix | 76 | 19 | 1 | 170,986 | 512 | 0 | 63,911 | 1.500 | 0 |
| 96 | async global LSN prefix | 76 | 19 | 1 | 482,888 | 131,072 | 50,875 | 62,985 | 1.500 | 0 |
| 96 | async dep frontier cstamp | 76 | 19 | 1 | 192,209 | 512 | 57 | 70,720 | 0.000 | 152 |

## 読み方

worker-wait global prefix はかなり遅い。total=48 では 122K tx/s で、async global prefix の 354K tx/s、async dep frontier cstamp の 209K tx/s より低い。これは worker が durable wait に直接巻き込まれると ERMIA の並行性が潰れることを示す。

async global LSN prefix は throughput だけ見ると強い。total=48 で 354K tx/s、total=96 で 483K tx/s 出る。ただし、これは巨大 backlog を抱えた状態での throughput である。total=48 の pending は 65,498、p99 は 131ms。つまり durable ack が詰まって、未 ack transaction を大量に積んでいる。

async dep frontier cstamp は peak ack tps では global prefix に負けるが、backlog と p99 を大きく抑える。total=48 で pending は 49、p99 は 512us。YCSB-B のように dependency が sparse な workload では、全 shard の global prefix を待たず、必要な dependency frontier だけ待てばよいことが効いている。

total=96 では no durability が 5.41M から 1.31M tx/s に落ちる。SMT まで使うと ERMIA 本体側のメモリ/NUMA/SMT 干渉が強く出るため、本文の main claim は total=24/48 を中心に置き、96 は robustness/appendix 扱いがよい。

## Max-pending sweep

condition: YCSB-B, total=48, worker=38, logger=9, committer=1

| mode | max_pending | ack tps | p99 us | pending | fdatasync/s |
|---|---:|---:|---:|---:|---:|
| async global LSN prefix | 1,024 | 360,354 | 3,686 | 1,038 | 47,428 |
| async global LSN prefix | 4,096 | 346,064 | 13,107 | 4,081 | 45,812 |
| async global LSN prefix | 16,384 | 355,730 | 45,875 | 16,352 | 47,089 |
| async global LSN prefix | 65,536 | 339,250 | 235,930 | 65,497 | 46,353 |
| async dep frontier cstamp | 1,024 | 209,847 | 512 | 52 | 38,773 |
| async dep frontier cstamp | 4,096 | 204,283 | 512 | 57 | 39,626 |
| async dep frontier cstamp | 16,384 | 211,863 | 512 | 48 | 39,492 |
| async dep frontier cstamp | 65,536 | 214,237 | 512 | 59 | 39,243 |

global prefix は max_pending を増やすほど tail latency が悪化する。throughput は 340K-360K tx/s 程度で高いが、p99 は 3.7ms から 236ms まで伸びる。つまり throughput を高く見せるには backlog を許している。

dep frontier cstamp は max_pending にほぼ依存せず、pending は 50 前後、p99 は 512us に収まる。こちらは peak throughput では低いが、durable ack backlog をほぼ作らない。

## Cstamp vs separate LSN

condition: YCSB-B, total=48, worker=38, logger=9, committer=1

| condition | mode | ack tps | p99 us | pending | atomic/tx | frontier bytes/tx | lsn alloc ns/tx |
|---|---|---:|---:|---:|---:|---:|---:|
| real I/O | dep frontier LSN | 209,401 | 512 | 48 | 1.500 | 72 | 227.4 |
| real I/O | dep frontier cstamp | 202,531 | 512 | 52 | 0.000 | 72 | 0.0 |
| I/O-light | dep frontier LSN | 210,800 | 1,024 | 112 | 1.500 | 72 | 219.5 |
| I/O-light | dep frontier cstamp | 205,525 | 1,024 | 96 | 0.000 | 72 | 0.0 |

cstamp-as-logical-LSN は WAL 側の separate global LSN allocation を消せている。atomic/tx は 1.5 から 0、lsn allocation は約 220ns/tx から 0 になる。

ただし、この結果では cstamp 版が throughput で大きく勝っているわけではない。論文上の主張は「cstamp にすると速い」ではなく、「ERMIA が既に持つ serialization order を recovery logical order として使えるため、separate global LSN の二重管理が不要になる」が正しい。

## YCSB-A representative point

condition: total=48, worker=38, logger=9, committer=1

| mode | ack tps | p99 us | pending | fdatasync/s | atomic/tx | frontier bytes/tx |
|---|---:|---:|---:|---:|---:|---:|
| no durability | 3,792,252 | 0 | 0 | 0 | 0.000 | 0 |
| worker-wait global prefix | 82,393 | 512 | 0 | 29,708 | 6.001 | 0 |
| async global LSN prefix | 206,871 | 262,144 | 65,546 | 29,482 | 6.000 | 0 |
| async dep frontier cstamp | 202,043 | 262,144 | 65,517 | 28,656 | 0.000 | 72 |

YCSB-A では dependency が密になり、dep frontier は global prefix に近づく。そのため YCSB-B のような pending/p99 改善は出ない。これは理論的に自然な結果で、dependency frontier の効果は sparse dependency で大きく、dense dependency では global prefix に近づく、という説明に使える。

## 論文で言えること

1. worker/flusher/committer separation は必要。worker-wait global prefix は total-budget でも明確に遅い。
2. async global LSN prefix は throughput は高いが、巨大 backlog と tail latency を作る。
3. dependency frontier は YCSB-B のような sparse dependency では backlog と p99 を大きく抑える。
4. cstamp-as-logical-LSN は separate WAL global LSN allocation を消せる。ただし現状の性能差は小さく、主張は correctness/design simplification を中心に置くべき。
5. dense dependency の YCSB-A では dep frontier は global prefix に近づく。この限界も正直に書く。

## 使ったコマンド

```bash
python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment total_budget \
  --seconds 5 \
  --repeats 5 \
  --total-values 1,2,4,8,16,24,32,48,96 \
  --total-workload ycsb_b \
  --skip-build

python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment max_pending \
  --seconds 5 \
  --repeats 5 \
  --fixed-total-threads 48 \
  --pareto-workloads ycsb_b \
  --max-pending-values 1024,4096,16384,65536 \
  --skip-build

python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment cstamp \
  --seconds 5 \
  --repeats 5 \
  --threads 48 \
  --fixed-total-threads 48 \
  --cstamp-workload ycsb_b \
  --skip-build

python3 scripts/run_ermia_cstamp_pwal_experiments.py \
  --experiment total_budget \
  --seconds 5 \
  --repeats 5 \
  --total-values 48 \
  --total-workload ycsb_a \
  --skip-build
```

