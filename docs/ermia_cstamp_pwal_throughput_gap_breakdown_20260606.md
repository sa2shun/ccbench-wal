# ERMIA Cstamp-PWAL throughput gap breakdown 2026-06-06

目的は、YCSB-B / 32 threads で `async_dep_frontier_cstamp` が `async_global_lsn_prefix` より peak ack throughput で遅い理由を counter で分解することです。

対象結果:

- CSV: `results/ermia_cstamp_pwal_20260606_184115_413672/breakdown/ermia_cstamp_pwal_breakdown_20260606_184115_413672.csv`
- 自動生成表: `results/ermia_cstamp_pwal_20260606_184115_413672/breakdown/ermia_cstamp_pwal_breakdown_20260606_184115_413672.md`

## 条件

| item | value |
|---|---|
| workload | YCSB-B |
| thread_num | 32 |
| logger_num | 8 |
| committer_num | 1 |
| group_size | 8 |
| flush_us | 100 |
| extime | 5 sec |
| repeats | 5 |
| max_pending | 32, 1024, 4096, 65536 |

比較 mode:

| mode | 目的 |
|---|---|
| `ermia_async_global_lsn_prefix` | safe conservative baseline |
| `ermia_async_dep_frontier_cstamp` | 提案方式 |
| `ermia_async_dep_frontier_cstamp_no_publish` | frontier は計算するが version publish しない |
| `ermia_async_dep_frontier_cstamp_zero_dep` | dependency は self log のみ。collect/publish なし |
| `ermia_async_dep_frontier_cstamp_prealloc` | waitlist 登録時の frontier copy を thread-local buffer に寄せる |

## 実装メモ

bounded-inflight 測定中に、`async_global_lsn_prefix` + `max_pending=32` で止まるケースがありました。原因は、global LSN を割り当ててから `max_pending` throttle に入っていたことです。

小さい LSN を持った transaction が enqueue 前に throttle で止まると、global prefix はその LSN を待ちます。しかしその log はまだ enqueue されていないため durable になれず、pending が減らず、worker も進めない状態になります。

修正として、async mode では `throttleAsyncPending()` を LSN allocation / local_seq allocation / payload build より前に移しました。これにより `max_pending=32` でも global prefix が完走します。

## 主要結果

### max_pending=65536

| mode | ack tps | p99 us | pending | commits/fdatasync |
|---|---:|---:|---:|---:|
| global prefix | 372,279 | 131,072 | 65,531 | 7.99 |
| dep frontier cstamp | 222,381 | 512 | 55 | 6.30 |
| no publish | 370,213 | 524,288 | 65,487 | 8.00 |
| zero dep | 360,341 | 314,573 | 65,537 | 8.00 |
| prealloc | 226,195 | 512 | 56 | 6.31 |

読み方:

- 通常の dep frontier cstamp は global prefix より ack tps が約 150K 低い。
- `no_publish` は 370K tps まで戻る。
- `zero_dep` も 360K tps まで戻る。
- `prealloc` は 226K tps で通常版とほぼ同じ。

したがって、遅さの主因は waitlist そのものではなく、frontier metadata の publish と、その publish された shared_ptr frontier を後続 transaction が collect するコストです。

### max_pending=32

| mode | ack tps | p99 us | pending | worker stall ns/tx |
|---|---:|---:|---:|---:|
| global prefix | 123,332 | 512 | 49 | 263,551 |
| dep frontier cstamp | 165,803 | 512 | 37 | 70,831 |
| no publish | 184,790 | 512 | 47 | 145,944 |
| zero dep | 185,188 | 512 | 48 | 150,340 |
| prealloc | 167,967 | 512 | 42 | 69,039 |

bounded-inflight では global prefix の方が worker stall が大きく、throughput も低くなります。これは global prefix が backlog を使えない条件では弱いことを示しています。

## per-transaction cost breakdown

### max_pending=65536

| component | global prefix | dep frontier cstamp | delta |
|---|---:|---:|---:|
| WAL enqueue ns/tx | 282 | 504 | +222 |
| frontier collect ns/tx | 0 | 47,892 | +47,892 |
| frontier merge ns/tx | 0 | 1,312 | +1,312 |
| frontier publish ns/tx | 0 | 97,628 | +97,628 |
| version install ns/tx | 144 | 143 | -1 |
| waitlist registration ns/tx | 47,854 | 8,968 | -38,885 |
| committer event ns/tx | 1,683 | 1,368 | -315 |
| write ns/tx | 7,098 | 5,693 | -1,405 |
| fdatasync ns/tx | 18,379 | 19,301 | +922 |
| worker stall ns/tx | 33,260 | 0 | -33,260 |
| queue wait us/acked tx | 221,018 | 329 | -220,689 |
| p99 durable ack us | 131,072 | 512 | -130,560 |

この表で重要なのは、dep frontier cstamp が増やしている主なコストが以下の 2 つであることです。

- `frontier_publish_ns/tx`: 約 97.6 us
- `frontier_collect_ns/tx`: 約 47.9 us

一方で、waitlist/committer は主因ではありません。むしろ `waitlist_registration_ns/tx` は global prefix の方が大きいです。

### metadata counters at max_pending=65536

| mode | frontier bytes/tx | nonzero entries/tx | read updates/tx | write updates/tx | alloc/tx | shared_ptr/tx | waitlist regs/tx | conditions/tx |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| global prefix | 0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 |
| dep frontier cstamp | 64 | 7.76 | 9.50 | 0.50 | 10.50 | 10.50 | 1.00 | 1.00 |
| no publish | 64 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 |
| zero dep | 0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 |
| prealloc | 64 | 7.77 | 9.50 | 0.50 | 10.50 | 10.50 | 1.00 | 1.00 |

通常の dep frontier cstamp では、1 transaction あたり平均で:

- read frontier update: 9.50 回
- write frontier update: 0.50 回
- frontier allocation: 10.50 回
- shared_ptr frontier creation/refcount target: 10.50 回

YCSB-B は 10 ops/tx かつ read-heavy なので、ほぼ全 read version に `read_frontier` を publish しています。これが重いです。

## ablation の読み方

### no_publish

`no_publish` は `max_pending=65536` で 370K tps まで戻りました。これは global prefix の 372K tps とほぼ同等です。

つまり、dep frontier ack protocol や cstamp logical LSN 自体が遅いのではなく、version への frontier publish が大きく throughput を落としています。

ただし、`no_publish` は dependency propagation を落とすため、一般には safe な方式ではありません。あくまで publish cost を測る ablation です。

### zero_dep

`zero_dep` も 360K tps まで戻りました。これは waitlist/committer の self-only path は global prefix 近くまで出せることを示しています。

したがって、waitlist data structure が第一主因とは言いにくいです。

### prealloc

`prealloc` は 226K tps で通常版 222K tps とほぼ同じです。

今回の prealloc は、waitlist 登録時の frontier copy を thread-local buffer に寄せるものです。しかし throughput は戻りませんでした。つまり、問題は log enqueue 側の frontier temporary allocation ではなく、version publish / shared_ptr frontier / 後続 collect 側です。

## batching の影響

`max_pending=65536` では:

| mode | fdatasync/s | commits/fdatasync | bytes/s |
|---|---:|---:|---:|
| global prefix | 48,613 | 7.99 | 44.4 MB/s |
| dep frontier cstamp | 35,293 | 6.30 | 25.6 MB/s |
| no publish | 48,335 | 8.00 | 45.0 MB/s |
| zero dep | 47,097 | 8.00 | 43.8 MB/s |
| prealloc | 35,829 | 6.31 | 26.1 MB/s |

dep frontier cstamp は `commits/fdatasync` が 6.30 まで下がっています。ただしこれは根本原因というより、frontier publish/collect が worker 側を遅くし、flusher に十分な batch が溜まりにくくなった結果と見ています。

根拠は、`no_publish` と `zero_dep` では `commits/fdatasync` が 8.00 に戻り、throughput も 360K-370K に戻るためです。

## 判断

今回の結果は、次の判断になります。

| candidate | judgment | evidence |
|---|---|---|
| frontier metadata publish | 主因 | `frontier_publish_ns/tx = 97.6 us`, `no_publish` が 370K tps まで回復 |
| shared_ptr/refcount/collect | 主因の一部 | `frontier_collect_ns/tx = 47.9 us`, `alloc/shared_ptr = 10.5 / tx` |
| waitlist/committer | 主因ではない | `zero_dep` が 360K tps、waitlist regs は 1/tx |
| batching | 二次的影響 | normal dep frontier は commits/fdatasync 6.3、no_publish/zero_dep は 8.0 |
| worker stall | global prefix 側の問題 | max_pending=65536 では global prefix が pending 65K / p99 131ms |

## 結論

`async_dep_frontier_cstamp` が `async_global_lsn_prefix` より peak ack throughput で遅い主因は、frontier metadata publish と shared_ptr frontier の collect/refcount cost です。

特に YCSB-B では read-heavy かつ 10 ops/tx なので、1 transaction あたり約 9.5 個の read version に `read_frontier` を publish しています。その結果、約 10.5 回/tx の frontier allocation / shared_ptr creation が発生し、さらに後続 transaction が非 null の frontier を atomic load / shared_ptr refcount / merge するため、collect 側にも約 47.9 us/tx のコストが出ています。

一方で、dependency frontier の waitlist/committer 自体は第一主因ではありません。`zero_dep` は global prefix とほぼ同じ throughput まで戻ります。

次の最適化候補は、優先順に以下です。

1. `read_frontier` update を毎 read version に即時 publish しない。
2. `shared_ptr<const WalFrontier>` をやめ、inline small frontier または epoch/batch frontier にする。
3. read frontier publish を遅延/集約する。
4. write frontier のみの conservative prototype を作り、read frontier cost を切り離す。
5. nonzero sparse frontier representation を使う。

論文上は、現状の結果をこう書くのが正確です。

```text
Dependency frontier removes global-prefix-induced backlog and tail latency,
but the naive ERMIA integration pays a large metadata propagation cost.
The throughput gap is dominated by read-frontier publication and shared frontier
object management, not by the durable waitlist itself.
```
