# YCSB-C read-only workload の再分析

date: 2026-06-08

## 結論

以前の YCSB-C 図で Single WAL / P-WAL / TideWAL に大きな差が出ていた主因は、
WAL 永続化方式の差ではなく、実験条件と実装経路の差だった。

今回、次を修正して測り直した。

1. Single WAL / P-WAL / TideWAL を同じ `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` で実行する
2. 横軸を total active threads ではなく transaction worker threads にする
3. 全 system で `CCBENCH_WAL_SKIP_READ_ONLY=1` を有効化する
4. TideWAL に read-only empty-frontier fast path を入れる

修正後、32 worker threads / YCSB-C では次の通り。

| system | worker | logger | committer | total active | ack tps | p99 us | pending | read-only fast path | waitlist reg ns/tx |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Single WAL | 32 | 0 | 0 | 32 | 802,237 | 39 | 0 | 0.000 | 0.0 |
| P-WAL | 32 | 32 | 0 | 32 | 794,168 | 40 | 0 | 0.000 | 0.0 |
| TideWAL | 32 | 7 | 1 | 40 | 783,114 | 41 | 0 | 1.000 | 0.0 |

したがって、修正後の YCSB-C では 3 system はほぼ同等である。
YCSB-C は read-only workload なので、WAL record / fdatasync は発生しない。
この結果は durability protocol の I/O 性能ではなく、read-only fast path overhead の sanity check として扱う。

## 何を直したか

### 1. 同一 binary で WAL mode を切り替える

以前は Single WAL と P-WAL/TideWAL が別 binary / 別 commit path だった。
YCSB-C では WAL I/O が消えるため、この差がそのまま read path / fast path / 計測 path の差として出ていた。

今回、`CCBENCH_WAL_MODE` を追加し、同じ ERMIA-PWAL binary 内で WAL mode を切り替えるようにした。

- `CCBENCH_WAL_MODE=shared`: Single WAL
- `CCBENCH_WAL_MODE=per_thread`: P-WAL / TideWAL

これにより、YCSB-C の Single WAL と P-WAL の差は大きく縮小した。

### 2. worker-thread 軸に戻す

以前の図は `total active threads = worker + logger + committer` を横軸にしていた。
しかし、通常の DB 実験としては transaction worker threads を横軸にする方が自然である。

今回の worker-thread 比較では、32 worker threads の TideWAL は追加で 7 logger threads と 1 committer thread を持つ。
したがって OS thread 数は 40 だが、横軸は transaction worker 数である。

重要なのは、YCSB-C では logger は実質的に起動せず、committer も condition variable で待機している点である。
つまり、read-only workload で background thread が WAL I/O を処理して速くしているわけではない。

### 3. TideWAL read-only empty-frontier fast path

TideWAL では read-only transaction でも、読んだ version の writer が未 durable の可能性を確認するために dependency frontier を collect する。
これは安全性のために必要である。

ただし、frontier が全ゼロなら待つべき durable dependency は存在しない。
以前はこの場合でも `registerDepAck()` に入り、request allocation / mutex / waitlist bookkeeping を実行していた。

今回、次の fast path を入れた。

```cpp
if (read_only && dep_frontier is empty_or_all_zero) {
  recordAckLatency(0);
  async_acked_commits++;
  return;
}
```

これにより、YCSB-C / TideWAL では全 read-only transaction が fast path に入り、
`waitlist_registration_ns_per_tx = 0` になった。

## 残っている overhead

TideWAL は read-only でも dependency frontier collect を行う。
32 worker threads / YCSB-C では:

| metric | value |
|---|---:|
| `frontier_collect_ns_per_tx` | 2,681.7 ns |
| `waitlist_registration_ns_per_tx` | 0.0 ns |
| `read_only_fast_path_per_tx` | 1.000 |

つまり、修正後に残っている TideWAL 固有 overhead は主に frontier collect である。

この collect は read-only response safety のために残している。
read-only transaction が未 durable writer の値を読んだ場合、その writer が durable になる前に client response を返すと、
crash 後に存在しない値を観測したことになるためである。

## idle / yield の確認

pure read-only workload では TideWAL の flusher は実質的に仕事がない。
今回の smoke では flusher idle counter は 0 で、これは read-only path で logger/flusher が起動されていないためである。

committer は起動するが、仕事がないときは condition variable で待機している。
2 秒 smoke では `committer_idle_wait_ns` が約 2 秒分記録されており、busy spin ではなく休んでいることを確認した。

## 今後の読み方

YCSB-C は main durability result ではない。
論文では次のように扱うのが安全である。

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
