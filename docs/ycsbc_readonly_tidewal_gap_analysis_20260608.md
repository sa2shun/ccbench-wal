# YCSB-C read-only workload で性能差が出る理由

date: 2026-06-08

## 問題

YCSB-C は read-only workload なので、`CCBENCH_WAL_SKIP_READ_ONLY=1` の条件では
Single WAL / P-WAL / TideWAL のどれも WAL record を書かないはずである。

それにもかかわらず、最新の fair total-thread-budget 結果では YCSB-C に性能差が出ている。

特に 32 total active threads では次の通り。

| system | total threads | worker | logger | committer | ack tps | p99 us | pending | read-only ratio | fdatasync ns/tx |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Single WAL | 32 | 32 | 0 | 0 | 793,706 | 40.2 | 0 | 1.000 | 0 |
| P-WAL | 32 | 32 | 32 | 0 | 1,443,722 | 21.8 | 0 | 1.000 | 0 |
| TideWAL | 32 | 24 | 7 | 1 | 723,000 | 64.0 | 0 | 1.000 | 0 |

結論から言うと、この差は WAL I/O / fdatasync の差ではない。
YCSB-C で見えているのは、read-only fast path の実装差、TideWAL の background thread 予約、
および dependency frontier bookkeeping の overhead である。

## まず確認したこと

最新 summary は `paper/tables/ycsbabc_tidewal_fair_total_20260607.csv`。
YCSB-C では全 system で次が成り立っている。

- `read_only_commits_per_tx = 1.000`
- `pending = 0`
- `fdatasync_ns_per_tx = 0`
- TideWAL でも `frontier_publish_ns_per_tx = 0`

raw result でも、32 total threads の各 mode は次の通り。

| system | wal bytes | fdatasync count | read-only commits |
|---|---:|---:|---:|
| Single WAL | 0 | 0 | all commits |
| P-WAL | 0 | 0 | all commits |
| TideWAL | 0 | 0 | all commits |

したがって、YCSB-C の throughput 差を「WAL 書き込みが速い / 遅い」と解釈してはいけない。

## 原因 1: TideWAL は fair total-thread 条件で worker 数が少ない

fair total-thread 実験では、TideWAL の thread budget は worker / flusher / committer に分けている。

設定は `scripts/run_ycsbabc_tidewal_fair_total.py` の `TOTAL_ALLOC`。

```python
TOTAL_ALLOC = {
    4: (2, 1, 1),
    8: (5, 2, 1),
    16: (12, 3, 1),
    24: (18, 5, 1),
    32: (24, 7, 1),
    48: (38, 9, 1),
    96: (76, 19, 1),
}
```

該当箇所:

- `scripts/run_ycsbabc_tidewal_fair_total.py:60`
- `scripts/run_ycsbabc_tidewal_fair_total.py:142`

32 total threads の場合:

```text
Single WAL: worker = 32
P-WAL:      worker = 32
TideWAL:    worker = 24, logger = 7, committer = 1
```

YCSB-C では read-only skip により logger / committer は実質的に仕事をしない。
しかし TideWAL は total-thread budget のために worker を 24 本に減らしている。
そのため、YCSB-C では TideWAL が最初から worker 数で不利になる。

32 total threads の worker あたり throughput は次のようになる。

| system | ack tps | workers | tx/s/worker |
|---|---:|---:|---:|
| Single WAL | 793,706 | 32 | 24,803 |
| P-WAL | 1,443,722 | 32 | 45,116 |
| TideWAL | 723,000 | 24 | 30,125 |

TideWAL は worker 数が少ないだけでなく、worker あたりでも P-WAL より遅い。
その追加原因が次の frontier / ack path overhead である。

## 原因 2: TideWAL は read-only でも dependency frontier を collect している

TideWAL では transaction begin 時に dependency frontier を初期化する。

```cpp
dep_frontier_.reset(ccbench::WalLogger::instance().shardCount());
```

該当箇所:

- `cc/ermia_pwal/transaction.cc:89`

さらに read では、読んだ version の `write_frontier_` を見て、自分の dependency frontier に merge する。

```cpp
mergeVersionFrontier(ver);
```

該当箇所:

- `cc/ermia_pwal/transaction.cc:176`

`mergeVersionFrontier()` の中では、version の `write_frontier_` を atomic load する。

```cpp
auto frontier = std::atomic_load_explicit(&ver->write_frontier_,
                                          std::memory_order_acquire);
```

該当箇所:

- `cc/ermia_pwal/transaction.cc:939`

YCSB-C では、read-only transaction は DB state を作らないため、frontier publish は skip されている。
これは正しい。

しかし、読んだ version の writer が未 durable である可能性を判定するには、read 時の dependency collect は必要である。
そのため、TideWAL は read-only でも collect cost を払う。

実測では 32 total threads / YCSB-C で:

```text
frontier_collect_ns_per_tx ~= 2,740 ns / tx
frontier_publish_ns_per_tx = 0
fdatasync_ns_per_tx        = 0
```

つまり、WAL 書き込みは消えているが、dependency frontier の read-side bookkeeping は残っている。

## 原因 3: TideWAL の read-only ack path が P-WAL より重い

Single WAL / P-WAL の read-only skip は非常に軽い。

`include/wal_logger.hh` の `logCommitWithFrontier()` では、worker-wait mode の read-only transaction は即 return する。

```cpp
if (skip_read_only_ && write_set.empty() && isWorkerWaitMode(durable_mode_)) {
  stats_.commits.fetch_add(1, std::memory_order_relaxed);
  stats_.read_only_commits.fetch_add(1, std::memory_order_relaxed);
  recordAckLatency(0);
  return WalCommitResult{0, 0, 0};
}
```

該当箇所:

- `include/wal_logger.hh:116`

一方、TideWAL では read-only transaction は `ackReadOnlyWithFrontier()` に入る。

```cpp
if (write_set_.empty() && ccbench::WalLogger::dependencyFrontierRequested()) {
  ccbench::WalLogger::instance().ackReadOnlyWithFrontier(thid_, &dep_frontier_);
}
```

該当箇所:

- `cc/ermia_pwal/transaction.cc:789`

`ackReadOnlyWithFrontier()` はログを書かないが、frontier をコピーして、最後に `registerDepAck()` を呼ぶ。

```cpp
dep = *dep_frontier;
dep.ensureSize(shardCount());
...
registerDepAck(0, 0, *dep_for_ack, enqueue_ns, false);
```

該当箇所:

- `include/wal_logger.hh:135`

`registerDepAck()` は global mutex を取り、request を作り、pending counter を更新し、
条件が満たされていれば即 ack する。

```cpp
std::lock_guard<std::mutex> guard(async_commit_mutex_);
auto request = std::make_shared<DepAckRequest>();
...
pending_async_acks_.fetch_add(1, std::memory_order_relaxed);
...
if (request->remaining == 0) {
  ackAsyncRequestLocked(request->enqueue_ns);
}
```

該当箇所:

- `include/wal_logger.hh:1097`

YCSB-C では dependency frontier は全ゼロなので、実際には待つべき条件はない。
raw counter でも:

```text
dep_wait_conditions_per_tx      = 0
waitlist_registrations_per_tx   = 0
pending                         = 0
```

しかし、`registerDepAck()` の mutex / allocation / counter / ack histogram 更新は毎 transaction で実行される。

32 total threads / YCSB-C / TideWAL の raw counter:

```text
frontier_collect_ns_per_tx      ~= 2.74 us
waitlist_registration_ns_per_tx ~= 19.2 us
ack_process_ns_per_tx           ~= 0.42 us
committer_queue_wait_us/tx      ~= 19.1 us
dep_wait_conditions_per_tx      = 0
fdatasync_count                 = 0
wal bytes                       = 0
```

重要なのは、ここでの `committer_queue_wait_us/tx` はストレージ待ちではないこと。
read-only transaction が即 ack されるまでの bookkeeping path を latency として記録している。

## 原因 4: P-WAL が Single WAL より速い理由も WAL ではない

YCSB-C では P-WAL が Single WAL よりかなり速い。
しかしこれも WAL I/O の差ではない。

短い再実行でも次の傾向が出た。

```text
32 workers / YCSB-C / 2 sec rerun

Single WAL:
  throughput ~= 652K tx/s
  bytes = 0
  fdatasync = 0
  tx_breakdown_read_ns ~= 26.2s
  tx_breakdown_wal_log_ns ~= 2.39s

P-WAL:
  throughput ~= 1.19M tx/s
  bytes = 0
  fdatasync = 0
  tx_breakdown_read_ns ~= 15.0s
  tx_breakdown_wal_log_ns ~= 1.45s
```

P-WAL と Single WAL は別 binary である。

- Single WAL: `build/cc/ermia_wal/ycsb_ermia_wal.exe`
- P-WAL/TideWAL: `build/cc/ermia_pwal/ycsb_ermia_pwal.exe`

YCSB-C では WAL path がほぼ消えるため、binary の read/commit path 差、コード配置、計測 path、
version layout 差などが見えてしまう。

したがって、YCSB-C で「P-WAL の durability が Single WAL より速い」と読むのは誤りである。

## 原因 5: latency の定義も完全には揃っていない

Single WAL / P-WAL は read-only fast path で `recordAckLatency(0)` を呼ぶため、`ack_latency_p99_us = 0` になる。
summary script は `p99 <= 0` の場合、closed-loop average latency に fallback している。

該当箇所:

- `scripts/run_ycsbabc_tidewal_fair_total.py:239`

TideWAL は `ackReadOnlyWithFrontier()` で enqueue から ack までの latency を実測する。

したがって、YCSB-C の latency graph は:

- Single WAL / P-WAL: fallback された closed-loop average
- TideWAL: measured durable-ack bookkeeping latency

になっている。

これは YCSB-C latency を paper main claim に使うには危険である。

## まとめ

YCSB-C の結果は次のように読むべき。

1. YCSB-C では全 system が read-only で、WAL record / fdatasync は発生しない。
2. したがって YCSB-C の throughput 差は durability protocol の I/O 性能差ではない。
3. TideWAL は fair total-thread budget のため、32 total threads でも worker は 24 本しかない。
4. TideWAL は read-only でも dependency frontier collect を行う。
5. TideWAL は dependency が空でも `registerDepAck()` の mutex / request allocation / pending / ack bookkeeping path を通る。
6. P-WAL と Single WAL の差も WAL I/O ではなく、binary / transaction path の差である。
7. YCSB-C の latency は mode 間で計測定義が揃っていない。

したがって、論文では YCSB-C を main durability result として強く使わない方がよい。
使うなら、read-only fast path overhead を見る補助実験として扱うべきである。

推奨する書き方:

```text
YCSB-C is a read-only workload. With read-only WAL skipping enabled, it does
not exercise WAL persistence. We therefore use YCSB-C only as a sanity check
for read-only fast-path overhead, not as evidence for durability throughput.
```

日本語では:

```text
YCSB-C は全 transaction が read-only であり、read-only WAL skip により
WAL record / fdatasync が発生しない。そのため、この結果は durability protocol
の性能差ではなく、read-only fast path と dependency frontier bookkeeping の
overhead を見る補助実験として扱う。
```

## 改善案

最初に入れるべき改善は、TideWAL の read-only empty frontier fast path である。

現在の TideWAL read-only path は、dependency frontier が全ゼロでも `registerDepAck()` に入る。
しかし、frontier が全ゼロなら、読んだ version に durable wait すべき writer が存在しない。
したがって、read-only response を待たせる必要はない。

安全な fast path:

```cpp
if (read_only && dep_frontier.empty_or_all_zero()) {
  record read-only commit;
  record ack latency 0;
  increment async ack counter;
  return;
}
```

ただし、次は消してはいけない。

- read 時の dependency frontier collect
- 非ゼロ dependency frontier の durable wait
- CC / SSN 用の read tracking

消してよいのは、dependency frontier が全ゼロであることが確認できた read-only transaction の
`registerDepAck()` 経路だけである。

さらに正確な比較をするなら、YCSB-C では TideWAL の worker 数を 32 にした worker-fixed 補助実験も取るとよい。
ただし、それは main fair total-thread result ではなく、read-only overhead の切り分け用に置くべきである。
