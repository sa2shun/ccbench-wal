# Perf / Flame Graph results for ERMIA WAL breakdown

Date: 2026-06-04

Branch: `wal-clean-abort0-framework`
Commit: `77ab82d`

## Setup

The host was changed by the user from:

```text
kernel.perf_event_paranoid = 4
```

to:

```text
kernel.perf_event_paranoid = 1
```

Per user instruction, this setting was not restored after the run.

FlameGraph scripts were cloned to:

```text
/home/sa2shun/FlameGraph
```

## Artifacts

Output directory:

```text
results/perf_flamegraph_20260604_1408/
```

Generated CPU Flame Graphs:

- `normal_wal_32.svg`
- `normal_pwal_32.svg`
- `abort0_wal_32.svg`
- `abort0_pwal_32.svg`

Generated Perf artifacts per case:

- `.data`
- `.perf`
- `.folded`
- `.report.txt`

## Commands

Example command for normal YCSB shared WAL:

```bash
env CCBENCH_WAL_DIR=results/perf_flamegraph_20260604_1408/wal_files \
  perf record -F 99 -g --call-graph dwarf \
  -o results/perf_flamegraph_20260604_1408/normal_wal_32.data -- \
  ./build/cc/ermia_wal/ycsb_ermia_wal.exe \
  --extime=5 --thread_num=32 --ycsb_tuple_num=100000 \
  --ycsb_max_ope=10 --ycsb_rratio=50 --ycsb_zipf_skew=0
```

Conversion:

```bash
perf script -i CASE.data > CASE.perf
/home/sa2shun/FlameGraph/stackcollapse-perf.pl CASE.perf > CASE.folded
/home/sa2shun/FlameGraph/flamegraph.pl CASE.folded > CASE.svg
perf report --stdio --no-children -i CASE.data > CASE.report.txt
```

## Perf limitations in this run

`perf record` worked, but kernel symbols were restricted:

```text
Kernel address maps (/proc/{kallsyms,modules}) were restricted.
kernel samples can't be resolved.
```

So kernel frames appear as `[unknown]` addresses in reports and Flame Graphs.

`sched:sched_switch` tracepoint sampling was still blocked:

```text
No permissions to read /sys/kernel/tracing/events/sched/sched_switch
```

Therefore these are CPU Flame Graphs, not off-CPU Flame Graphs. They show where CPU cycles were sampled. They do not fully account for blocked wall-clock time in futex waits or `fdatasync`.

## Top CPU samples

### normal_ycsb, ERMIA+WAL, 32 threads

Perf data:

```text
759 samples
```

Top symbols:

```text
23.36% worker
19.95% TxExecutor::ssn_parallel_commit
 7.21% TxExecutor::install_version
 2.82% YcsbWorkload::run<TxExecutor, TransactionStatus>
 2.78% MasstreeWrapper<Tuple>::get_value
```

Relevant folded stacks include:

```text
worker;YcsbWorkload::run;TxExecutor::commit;TxExecutor::ssn_parallel_commit;___pthread_mutex_lock
worker;YcsbWorkload::run;TxExecutor::commit;TxExecutor::ssn_parallel_commit;__GI___libc_write
worker;YcsbWorkload::run;TxExecutor::commit;TxExecutor::ssn_parallel_commit;__GI_fdatasync
```

The CPU profile is centered around `ssn_parallel_commit`, which is where the shared WAL path is reached. The earlier `strace -f -c` run showed this case as `94.87% futex`, so the wall-clock bottleneck is still the shared WAL mutex wait rather than user-level payload construction.

### normal_ycsb, ERMIA+P-WAL, 32 threads

Perf data:

```text
7353 samples
```

Top symbols:

```text
27.79% [kernel unknown]
10.11% ccbench::WalLogger::logPerThread<...>
 5.02% pthread_mutex_lock
 3.16% pthread_mutex_unlock
 2.58% TxExecutor::ssn_parallel_commit
 1.45% __sched_yield
```

Relevant folded stacks include:

```text
worker;YcsbWorkload::run;TxExecutor::commit;TxExecutor::ssn_parallel_commit;ccbench::WalLogger::logPerThread
worker;YcsbWorkload::run;TxExecutor::commit;TxExecutor::ssn_parallel_commit;ccbench::WalLogger::logPerThread;__GI___libc_write
```

The CPU profile moves into per-thread WAL logging and kernel work below write/sync paths. The earlier syscall profile showed:

```text
49.31% fdatasync
22.94% sched_yield
20.50% write
 1.98% futex
```

So P-WAL removes most shared futex/mutex waiting and shifts cost to WAL I/O plus durable-prefix waiting/yielding.

### abort0_ycsb, ERMIA+WAL, 32 threads

Perf data:

```text
715 samples
```

Top symbols:

```text
34.67% worker
 8.50% MasstreeWrapper<Tuple>::get_value
 6.94% TxExecutor::read_internal
```

The syscall profile for the same shape was:

```text
95.01% futex
 1.04% write
 0.52% fdatasync
```

This again points to shared WAL mutex/futex waiting as the wall-clock bottleneck.

### abort0_ycsb, ERMIA+P-WAL, 32 threads

Perf data:

```text
6990 samples
```

Top symbols:

```text
30.99% [kernel unknown]
10.97% ccbench::WalLogger::logPerThread<...>
 3.88% pthread_mutex_lock
 3.60% pthread_mutex_unlock
 1.62% TxExecutor::update
 1.42% TxExecutor::ssn_parallel_commit
 0.86% __sched_yield
```

Relevant folded stacks include:

```text
worker;PartitionedYcsbWorkload::run;TxExecutor::commit;TxExecutor::ssn_parallel_commit;ccbench::WalLogger::logPerThread
worker;PartitionedYcsbWorkload::run;TxExecutor::commit;TxExecutor::ssn_parallel_commit;ccbench::WalLogger::logPerThread;__GI___libc_write
```

The syscall profile was:

```text
51.96% fdatasync
22.08% sched_yield
18.65% write
 2.33% futex
```

This matches the normal_ycsb P-WAL result: the shared WAL mutex is no longer the dominant cost; the remaining burden is WAL I/O and notification/order waiting.

## Overall conclusion

Perf/Flame Graph confirms the application-level location of the cost:

- Shared WAL samples concentrate under `TxExecutor::ssn_parallel_commit`, with lock/write/sync stacks under that path.
- External syscall profiling shows shared WAL wall-clock time is dominated by futex wait.
- P-WAL samples concentrate under `ccbench::WalLogger::logPerThread`.
- P-WAL greatly reduces futex dominance; the visible cost shifts to `fdatasync`, `write`, kernel work under those syscalls, and `sched_yield`/notify-prefix waiting.

For a cleaner wait-time picture, run off-CPU Flame Graphs on a host where tracepoints under `/sys/kernel/tracing/events/sched/` are readable.

## Off-CPU Flame Graph results

After the CPU Flame Graph run, the host was further opened for scheduler tracepoints:

```text
kernel.perf_event_paranoid = -1
kernel.kptr_restrict = 0
kernel.sched_schedstats = 1
```

The following tracefs files also had to be world-readable for `perf record` to write the `TRACING_DATA` feature correctly:

```text
/sys/kernel/tracing/printk_formats
/sys/kernel/tracing/saved_cmdlines
```

Without those permissions, `perf record` appeared to succeed, but `perf script` failed with:

```text
incompatible file format
```

and verbose `perf record` showed:

```text
failed to write feature TRACING_DATA
```

Off-CPU output directory:

```text
results/offcpu_flamegraph_20260604_1425/
```

Generated off-CPU Flame Graphs:

- `normal_wal_32_offcpu.svg`
- `normal_pwal_32_offcpu.svg`
- `abort0_wal_32_offcpu.svg`
- `abort0_pwal_32_offcpu.svg`

The off-CPU recording command shape was:

```bash
perf record -m 1024 -g \
  -e sched:sched_switch \
  -e sched:sched_stat_sleep \
  -e sched:sched_stat_blocked \
  -o CASE_offcpu.data -- BENCHMARK ...
```

Conversion:

```bash
perf script -F time,comm,pid,tid,event,ip,sym,dso,trace \
  -i CASE_offcpu.data > CASE_offcpu.perf

/home/sa2shun/FlameGraph/stackcollapse-perf-sched.awk -v recurse=1 \
  CASE_offcpu.perf > CASE_offcpu.folded

/home/sa2shun/FlameGraph/flamegraph.pl --color=io --countname=us \
  CASE_offcpu.folded > CASE_offcpu.svg
```

### Off-CPU category summary

The folded values are microseconds.

```text
normal_ycsb ERMIA+WAL
  total off-CPU: 96.299 s
  futex:         91.537 s  95.05%
  fdatasync:      1.761 s   1.83%
  nanosleep:      3.000 s   3.12%

normal_ycsb ERMIA+P-WAL
  total off-CPU: 52.965 s
  fdatasync:     45.840 s  86.55%
  write:          2.449 s   4.62%
  futex:          1.674 s   3.16%
  nanosleep:      3.000 s   5.66%

abort0_ycsb ERMIA+WAL
  total off-CPU: 97.752 s
  futex:         92.896 s  95.03%
  fdatasync:      1.856 s   1.90%
  nanosleep:      3.000 s   3.07%

abort0_ycsb ERMIA+P-WAL
  total off-CPU: 55.145 s
  fdatasync:     47.224 s  85.64%
  write:          2.588 s   4.69%
  futex:          2.331 s   4.23%
  nanosleep:      3.000 s   5.44%
```

### Off-CPU interpretation

The off-CPU Flame Graphs directly confirm the wait-time bottleneck:

- Shared WAL is dominated by `__GI___lll_lock_wait -> futex_wait`, about 95% of captured off-CPU time.
- P-WAL is dominated by `fdatasync -> ext4_sync_file -> jbd2_log_wait_commit` and page writeback waits, about 86% of captured off-CPU time.
- P-WAL futex wait is reduced to about 3-4% of captured off-CPU time.
- `clock_nanosleep` appears as benchmark driver sleep/measurement time and should not be interpreted as WAL cost.

This is the strongest confirmation so far: shared WAL primarily waits on the shared mutex, while P-WAL trades that wait for storage sync/writeback latency.
