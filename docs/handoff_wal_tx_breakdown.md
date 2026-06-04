# WAL/ERMIA TX Breakdown Handoff

This branch is `wal-clean-abort0-framework`.

## Why this exists

The goal was to preserve the current WAL/P-WAL profiling work before moving off this server.  The work focuses on explaining why ERMIA+WAL and ERMIA+P-WAL reach their observed throughput, especially under:

- normal YCSB
- abort-0 / partitioned YCSB
- thread counts: 1, 2, 4, 8, 16, 32

The main conclusion from the current run is:

- shared WAL is dominated by `mutex wait`
- P-WAL removes most shared-mutex waiting
- after that, P-WAL is mostly limited by `fdatasync` and `notify wait`

## Code added

### Transaction breakdown profiler

Added `include/tx_breakdown_profiler.hh`.

It prints process-exit counters named `tx_breakdown_*_ns`:

- `tx_breakdown_read_ns`
- `tx_breakdown_update_ns`
- `tx_breakdown_insert_ns`
- `tx_breakdown_delete_ns`
- `tx_breakdown_abort_cleanup_ns`
- `tx_breakdown_maintenance_ns`
- `tx_breakdown_ssn_finalize_pi_ns`
- `tx_breakdown_ssn_finalize_eta_ns`
- `tx_breakdown_ssn_exclusion_ns`
- `tx_breakdown_node_validation_ns`
- `tx_breakdown_wal_log_ns`
- `tx_breakdown_version_install_ns`
- `tx_breakdown_commit_cleanup_ns`
- `tx_breakdown_total_accounted_ns`

The timers are currently wired only into:

- `cc/ermia_wal/transaction.cc`
- `cc/ermia_pwal/transaction.cc`

They are intentionally independent from `ADD_ANALYSIS`, because the current build prints `ADD_ANALYSIS 0`.

### Existing WAL logger counters used

`include/wal_logger.hh` already prints:

- `wal_stats_payload_build_ns`
- `wal_stats_mutex_wait_ns`
- `wal_stats_write_ns`
- `wal_stats_fdatasync_ns`
- `wal_stats_notify_wait_ns`
- `wal_stats_total_accounted_ns`

The breakdown scripts combine `tx_breakdown_*` and `wal_stats_*`.

## Scripts added

Primary scripts:

```bash
python3 scripts/run_tx_breakdown.py
python3 scripts/plot_tx_breakdown_matplotlib.py results/tx_breakdown_YYYYMMDD_HHMMSS.txt
```

Additional SVG-only scripts kept for reference:

```bash
python3 scripts/plot_tx_breakdown.py results/tx_breakdown_YYYYMMDD_HHMMSS.txt
python3 scripts/plot_tx_breakdown_simple.py results/tx_breakdown_YYYYMMDD_HHMMSS.txt
```

`run_tx_breakdown.py` runs 24 cases:

- experiments: `normal_ycsb`, `abort0_ycsb`
- protocols: `ermia_wal`, `ermia_pwal`
- threads: `1, 2, 4, 8, 16, 32`
- `extime=5`
- `ycsb_tuple_num=100000`
- `ycsb_max_ope=10`
- `ycsb_rratio=50`
- `ycsb_zipf_skew=0`

## Preserved result artifacts

Because `results/` is gitignored, representative results were copied to `docs/tx_breakdown_artifacts/`:

- `tx_breakdown_20260528_112958.txt`
- `tx_breakdown_20260528_112958_matplotlib.svg`
- `tx_breakdown_20260528_112958_matplotlib.png`
- `tx_breakdown_20260528_112958_simple.svg`

Use the matplotlib SVG/PNG first; it is the clearest graph.

## Current representative 32-thread numbers

From `tx_breakdown_20260528_112958.txt`:

```text
normal_ycsb
ERMIA+WAL   tps=3181   abort=0.0014  ERMIA other=6.7664%  SSN=11.7941%  WAL=81.5546%
ERMIA+P-WAL tps=42070  abort=0.0089  ERMIA other=3.7517%  SSN=0.9755%   WAL=93.3527%

abort0_ycsb
ERMIA+WAL   tps=4278   abort=0       ERMIA other=0.1838%  SSN=0.0074%   WAL=99.9202%
ERMIA+P-WAL tps=42662  abort=0       ERMIA other=2.3764%  SSN=0.3054%   WAL=95.3538%
```

For WAL internals at 32 threads:

```text
normal_ycsb
ERMIA+WAL   wal_mutex_wait=78.7278%  wal_fdatasync=2.2968%   wal_notify_wait=0.0000%
ERMIA+P-WAL wal_mutex_wait=0.0510%   wal_fdatasync=55.4341%  wal_notify_wait=20.5654%

abort0_ycsb
ERMIA+WAL   wal_mutex_wait=97.0251%  wal_fdatasync=2.3585%   wal_notify_wait=0.0000%
ERMIA+P-WAL wal_mutex_wait=0.0513%   wal_fdatasync=57.0261%  wal_notify_wait=20.8057%
```

## What `notify wait` means

In the current P-WAL prototype, after a transaction writes and `fdatasync`s its own worker-local WAL file, it still waits until the global durable prefix reaches its commit LSN.

That waiting time is `wal_stats_notify_wait_ns`.

Conceptually:

```text
notify wait = time after local log flush until the transaction is allowed to return commit to the client
```

It exists because P-WAL can flush worker-local WAL files out of global LSN order. The current implementation still uses a global-prefix safety condition, so one worker may wait for smaller LSNs from other workers before notifying commit completion.

## Matplotlib note

The last server did not have `pip`, `python3-venv`, or sudo access for apt install. I temporarily used `apt-get download` and local `.deb` extraction to run matplotlib, but those local dependency files were not committed.

On a fresh server, prefer one of these:

```bash
sudo apt-get update
sudo apt-get install -y python3-matplotlib
```

or:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install matplotlib
```

Then run:

```bash
python3 scripts/plot_tx_breakdown_matplotlib.py docs/tx_breakdown_artifacts/tx_breakdown_20260528_112958.txt
```

## Validation already done

Build passed after adding the profiler:

```bash
cmake --build build --target ycsb_ermia_wal.exe ycsb_ermia_pwal.exe ycsb_abort0_ermia_wal.exe ycsb_abort0_ermia_pwal.exe -j2
```

The 24-case breakdown experiment completed without failed rows.

## Caveats / next work

- The profiler is self-instrumentation, not perf. Use it for application-level phase attribution.
- `strace -f -c` was previously used as an external sanity check: shared WAL showed futex dominance, while P-WAL shifted cost toward `fdatasync`/write.
- `perf`/FlameGraph were blocked by `kernel.perf_event_paranoid=4` on the old server.
- The current `TxBreakdownProfiler` prints at process exit through a static destructor. This is fine for benchmark runs, but not a production logging interface.
- The grouping in `run_tx_breakdown.py` is intentionally simple:
  - `SSN = ssn_finalize_pi + ssn_finalize_eta + ssn_exclusion`
  - `WAL = tx_breakdown_wal_log`
  - `ERMIA other = read/update/insert/delete/abort_cleanup/maintenance/node_validation/version_install/commit_cleanup`
- If more precision is needed, split `version_install` into eta update, pi update, status install, GC enqueue, and Masstree delete/remove.
