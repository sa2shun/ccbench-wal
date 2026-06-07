# ERMIA Cstamp-PWAL bottleneck report 2026-06-06

このレポートは、ERMIA に接続した Cstamp-PWAL について、次の 5 点を確認した結果をまとめたものです。

1. dependency frontier を `logger_num` サイズに軽量化した。
2. ERMIA 本測定を取り直した。
3. `max_pending` sweep を取り、throughput-latency/backlog の関係を見た。
4. ERMIA 接続用の deterministic correctness test を作った。
5. I/O-light 条件で cstamp-as-logical-LSN と separate LSN を比較した。

関連する生データと自動生成グラフは以下です。

- 本測定: `results/ermia_cstamp_pwal_20260606_162355_408754/workloads/ermia_cstamp_pwal_workloads_20260606_162355_408754.md`
- worker/flusher/committer 比率 sweep: `results/ermia_cstamp_pwal_20260606_160308_601999/ratio/ermia_cstamp_pwal_ratio_20260606_160308_601999.md`
- `max_pending` sweep: `results/ermia_cstamp_pwal_20260606_160838_604573/max_pending/ermia_cstamp_pwal_max_pending_20260606_160838_604573.md`
- cstamp vs separate LSN: `results/ermia_cstamp_pwal_20260606_170350_556374/cstamp/ermia_cstamp_pwal_cstamp_20260606_170350_556374.md`
- correctness test: `results/ermia_cstamp_pwal_20260606_162355_408754/ermia_cstamp_pwal_correctness.md`

## 結論

現時点の勝ち筋は、Cstamp-PWAL が常に最大 throughput を出すことではありません。主張すべきことは次です。

`global LSN prefix` は安全ですが、async pipeline と組み合わせると大量の durable ack backlog と tail latency を作ります。`dependency frontier` は、依存している WAL shard だけを待つため、YCSB-B のような sparse dependency workload では pending と p99 latency を大きく抑えます。さらに、ERMIA の `cstamp` を logical LSN として使うことで、WAL 側の separate global LSN allocation を消せます。

## 実装変更

### 1. frontier を logger_num サイズへ軽量化

以前は frontier が固定長 256 entries で、`uint64_t * 256 = 2048 bytes/tx` でした。今回、`WalFrontier` を dynamic vector に変更し、実際の `logger_num` だけ保持するようにしました。

今回の本測定では `logger_num=8` なので、frontier metadata は次です。

| old | new |
|---:|---:|
| 2048 bytes/tx | 64 bytes/tx |

これは dep frontier 系 mode の表でも `frontier bytes/tx = 64.0` として確認できます。

### 2. worker / flusher / committer separation

現在の pipeline は次の形です。

```text
worker
  -> WAL shard queue に enqueue
flusher
  -> 自分の WAL shard を batch write + fdatasync
  -> 自分専用の durable event queue に通知
committer
  -> flusher ごとの event queue を読む
  -> global prefix または dependency frontier 条件を満たした tx に ack
```

`committer_num` は `CCBENCH_WAL_COMMITTER_NUM` で指定できます。今回の本測定では、比率 sweep の結果をもとに `worker=32`, `flusher/logger=8`, `committer=1` を使いました。

## flusher / committer 比率

32 worker で `logger_num=1,2,4,8` と `committer_num=1,2` を振りました。代表値は YCSB-B / `async_dep_frontier_cstamp` です。

| logger_num | committer_num | ack tps | p99 us | pending | frontier bytes/tx |
|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 68,069 | 43,691 | 4,107 | 8 |
| 2 | 2 | 120,361 | 32,768 | 4,110 | 16 |
| 4 | 2 | 183,311 | 24,576 | 2,194 | 32 |
| 8 | 1 | 185,083 | 512 | 54 | 64 |
| 8 | 2 | 184,529 | 512 | 59 | 64 |

読み方は明確です。32 worker では `logger_num=8`、つまり worker:flusher はおおよそ 4:1 がよいです。`committer_num=2` にしても大きな改善はなく、現状では `committer_num=1` で十分です。

## ERMIA 本測定

条件:

| item | value |
|---|---|
| threads | 1, 2, 4, 8, 16, 32 |
| repeats | 5 |
| extime | 5 sec |
| logger_num | 8 |
| committer_num | 1 |
| group_size | 8 |
| flush_us | 100 |
| max_pending | 65,536 |
| workloads | abort0_write1, YCSB-A, YCSB-B |

32 thread の要点だけ抜粋します。

### abort0_write1 / 32 threads

| mode | ack tps | p99 us | pending | atomic/tx | frontier bytes/tx |
|---|---:|---:|---:|---:|---:|
| no durability | 5,304,583 | 0 | 0 | 0.000 | 0 |
| worker-wait global prefix | 106,679 | 512 | 0 | 2.000 | 0 |
| async global LSN prefix | 330,710 | 235,930 | 65,547 | 2.000 | 0 |
| async dep frontier LSN | 350,536 | 262,144 | 65,546 | 2.000 | 64 |
| async dep frontier cstamp | 334,705 | 262,144 | 65,545 | 0.000 | 64 |

abort0 では dependency がほぼない一方で、worker が非常に速く進むため、async 系は `max_pending` に張り付きます。ここでは dep frontier が tail latency を改善するというより、worker-wait を外す効果が主に出ています。

### YCSB-A / 32 threads

| mode | ack tps | p99 us | pending | abort rate | atomic/tx | frontier bytes/tx |
|---|---:|---:|---:|---:|---:|---:|
| no durability | 3,150,839 | 0 | 0 | 0.0045 | 0.000 | 0 |
| worker-wait global prefix | 73,060 | 512 | 0 | 0.0131 | 6.000 | 0 |
| async global LSN prefix | 220,085 | 262,144 | 65,530 | 0.0058 | 6.000 | 0 |
| async dep frontier LSN | 217,091 | 262,144 | 63,388 | 0.0040 | 5.999 | 64 |
| async dep frontier cstamp | 213,274 | 262,144 | 65,507 | 0.0046 | 0.000 | 64 |

YCSB-A は write が多く dependency が密なので、dep frontier は global prefix に近づきます。ここで「Cstamp-PWAL が常に速い」とは言えません。ただし `cstamp` 版は WAL 側の global LSN atomic allocation を 0 にできています。

### YCSB-B / 32 threads

| mode | ack tps | p99 us | pending | abort rate | atomic/tx | frontier bytes/tx |
|---|---:|---:|---:|---:|---:|---:|
| no durability | 4,168,310 | 0 | 0 | 0.0001 | 0.000 | 0 |
| worker-wait global prefix | 105,977 | 512 | 0 | 0.0003 | 1.501 | 0 |
| async global LSN prefix | 365,320 | 131,072 | 65,522 | 0.0001 | 1.500 | 0 |
| async dep frontier LSN | 226,562 | 512 | 47 | 0.0000 | 1.500 | 64 |
| async dep frontier cstamp | 231,444 | 512 | 48 | 0.0000 | 0.000 | 64 |

ここが一番重要です。throughput だけ見ると global prefix は 365K tps で高いですが、pending は 65,522、p99 は 131 ms です。一方で dep frontier cstamp は 231K tps ですが、pending は 48、p99 は 512 us です。

つまり YCSB-B では、global prefix は throughput を出す代わりに巨大 backlog を作っています。dependency frontier は backlog と tail latency を抑えています。

## max_pending sweep

条件:

| item | value |
|---|---|
| workload | YCSB-B |
| threads | 32 |
| logger_num | 8 |
| committer_num | 1 |
| compared modes | async_global_lsn_prefix, async_dep_frontier_cstamp |

| mode | max_pending | ack tps | p99 us | pending |
|---|---:|---:|---:|---:|
| dep frontier cstamp | 1,024 | 188,214 | 512 | 49 |
| dep frontier cstamp | 4,096 | 186,263 | 512 | 52 |
| dep frontier cstamp | 16,384 | 181,780 | 512 | 56 |
| dep frontier cstamp | 65,536 | 180,138 | 512 | 56 |
| global LSN prefix | 1,024 | 306,020 | 2,048 | 1,010 |
| global LSN prefix | 4,096 | 286,195 | 8,192 | 4,072 |
| global LSN prefix | 16,384 | 286,587 | 32,768 | 16,375 |
| global LSN prefix | 65,536 | 283,443 | 131,072 | 65,507 |

読み方:

- global prefix は `max_pending` を増やすと backlog がほぼ上限まで膨らみ、p99 も 2ms -> 8ms -> 32ms -> 131ms と悪化します。
- dep frontier cstamp は `max_pending` を増やしても pending は 50 前後、p99 は 512 us のままです。

この図で言えることは、global prefix は throughput を出すために巨大 backlog を必要としやすく、dep frontier は少ない pending で安定した durable ack を返せる、ということです。

## correctness test

deterministic model で、次のケースを確認しました。

```text
U writes x
T reads x and writes y
U -> T
physical flush order:
  T durable first
  U durable later
```

結果:

| mode | independent reversed flush | dependent reversed flush | transitive frontier | acked recovered | dependency violation |
|---|---|---|---|---|---|
| local-only | pass | fail | pass | no | yes |
| global-lsn-prefix | pass | pass | pass | yes | no |
| dep-frontier-lsn | pass | pass | pass | yes | no |
| dep-frontier-cstamp | pass | pass | pass | yes | no |

これは full crash recovery test ではありませんが、ERMIA frontier 接続で最低限必要な dependency-closed durable ack 条件は確認できています。

## cstamp vs separate LSN

条件:

| condition | meaning |
|---|---|
| real_io | fdatasync あり |
| io_light | fdatasync skip、group_size/flush_us を大きくした I/O bottleneck 弱め条件 |

32 thread の結果:

| condition | mode | ack tps | p99 us | pending | atomic/tx | lsn alloc ns/tx |
|---|---|---:|---:|---:|---:|---:|
| real_io | dep frontier LSN | 331,370 | 262,144 | 65,543 | 2.000 | 262.0 |
| real_io | dep frontier cstamp | 335,843 | 262,144 | 65,550 | 0.000 | 0.0 |
| io_light | dep frontier LSN | 626,988 | 1,024 | 400 | 2.000 | 247.8 |
| io_light | dep frontier cstamp | 673,074 | 1,024 | 400 | 0.000 | 0.0 |

real I/O では fdatasync/backlog が強く、cstamp と separate LSN の throughput 差は小さいです。一方、I/O-light では cstamp 版が 673K tps、LSN 版が 627K tps で、約 7.4% 高いです。

ここで重要なのは、cstamp 版が `atomic/tx = 0`、`lsn alloc ns/tx = 0` になっていることです。つまり ERMIA の `cstamp` を logical LSN として使うことで、WAL 側の separate global LSN allocation を消せています。

## 最終的な読み方

今回の結果から論文で言えることは次です。

1. worker-wait 型の WAL は ERMIA の並行性を潰す。32 threads YCSB-B では worker-wait global prefix が 106K tps、async global prefix が 365K tps。
2. async global prefix は throughput は出るが、巨大 backlog と tail latency を作る。32 threads YCSB-B では pending 65,522、p99 131ms。
3. dependency frontier は sparse dependency workload で backlog/tail を強く抑える。32 threads YCSB-B では dep frontier cstamp が pending 48、p99 512us。
4. dependency が dense な YCSB-A では dep frontier は global prefix に近づく。これは理論的に自然で、常勝主張はしない方がよい。
5. cstamp-as-logical-LSN は separate global LSN を不要にできる。real I/O では性能差は小さいが、I/O-light では 32 threads で 626,988 -> 673,074 tps に改善した。

したがって、主張は次の形にするのがよいです。

```text
ERMIA は cstamp で serialization order をすでに持っている。
WAL 側で separate global LSN prefix を再導入すると、durable ack backlog と tail latency を作る。
Cstamp-PWAL は cstamp を logical LSN として使い、
actual dependency frontier が durable になったときだけ ack を返す。
これにより、sparse dependency workload では安全性を保ったまま
global prefix の過剰な wait/backlog を避けられる。
```
