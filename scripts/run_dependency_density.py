#!/usr/bin/env python3
"""Dependency-density experiment.

Zipfian skew changes which keys are hot but not which WAL shards wrote
them, so it does not change the width of a transaction's dependency
frontier.  We instead vary the frontier width directly with an
abort-free partitioned workload: each worker owns a key partition, and a
remote-access probability controls how often a transaction touches other
partitions.  With remote_ppm = 0 every transaction depends only on its
local shard (a sparse frontier); with remote accesses the frontier
becomes dense.  Because the workload is abort-free, the dependency-frontier
effect is isolated from abort behaviour.

Both acknowledgment policies use the same asynchronous WAL pipeline and
differ only in the acknowledgment condition (as in the ack-policy
ablation).
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "build" / "cc" / "ermia_pwal" / "ycsb_abort0_ermia_pwal.exe"
TABLE_DIR = ROOT / "paper" / "tables"
RESULTS = ROOT / "results"

POLICIES = {
    "Global-prefix": "async_global_lsn_prefix",
    "Ayame": "async_dep_frontier_cstamp",
}
POLICY_ORDER = ["Global-prefix", "Ayame"]
POLICY_LABEL = {"Global-prefix": "Global-prefix", "Ayame": "Dep.\\ frontier (Ayame)"}
# remote read probability in ppm -> percentage label
REMOTES = [0, 10000, 100000, 1000000]


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        m = re.match(r"^#?([A-Za-z0-9_\[\]]+):\s*(.*)$", line)
        if m:
            row[m.group(1)] = m.group(2).strip()
    return row


def run_case(out_dir, mode_value, remote_ppm, repeat, args):
    wal_dir = out_dir / "wal"
    # The WAL dir is shared across all runs; clear it before each run so the
    # log files do not accumulate (high-throughput runs write ~1 GB each and
    # would otherwise fill the disk over the full sweep).
    shutil.rmtree(wal_dir, ignore_errors=True)
    wal_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "CCBENCH_WAL_DIR": str(wal_dir),
        "CCBENCH_WAL_SKIP_READ_ONLY": "1",
        "CCBENCH_WAL_MODE": "per_thread",
        "CCBENCH_WAL_DURABLE_MODE": mode_value,
        "CCBENCH_WAL_LOGGER_NUM": "9",
        "CCBENCH_WAL_COMMITTER_NUM": "1",
        "CCBENCH_WAL_GROUP_SIZE": str(args.group_size),
        "CCBENCH_WAL_FLUSH_US": str(args.flush_us),
        "CCBENCH_WAL_MAX_PENDING": str(args.max_pending),
    })
    cmd = [
        "numactl", "--interleave=all",
        str(EXE), f"--thread_num={args.workers}", f"--extime={args.seconds}",
        "--clocks_per_us=1800", "--ycsb_tuple_num=100000", "--ycsb_max_ope=10",
        f"--ycsb_remote_read_prob_ppm={remote_ppm}",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    m = parse_metrics(proc.stdout)
    acked = float(m.get("wal_stats_async_acked_commits", 0) or 0)
    ext = float(m.get("actual_extime", 0) or args.seconds)
    commits = float(m.get("wal_stats_commits", 0) or 0)
    nz = float(m.get("wal_stats_dep_frontier_nonzero_entries", 0) or 0)
    return {
        "ack_tps": acked / ext if ext else 0.0,
        "p99_us": float(m.get("wal_stats_ack_latency_p99_us", 0) or 0),
        # Snapshot pending at the measurement stop, matching the main summary
        # table (not the transient peak max_pending_commits).
        "pending": float(m.get("wal_stats_measurement_pending_commits", 0) or 0),
        "nz_per_tx": nz / commits if commits else 0.0,
    }


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def kfmt(v):
    return f"{v/1000:.1f}K"


def commafmt(v):
    return f"{v:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=5)
    # The abort-free workload commits (and logs) on every transaction, so its
    # per-second WAL volume is roughly twice that of the mixed YCSB workloads
    # (which skip read-only transactions).  With the default group of 16 the
    # fdatasync-bound flush pipeline saturates for *both* acknowledgment
    # policies, hiding the effect we want to isolate.  A flush group of 64
    # keeps the pipeline unsaturated for both policies, so the remaining
    # difference reflects only the acknowledgment rule.  The result is robust
    # to the exact value (64-1024 all drain the dependency-frontier rule while
    # global-prefix stays saturated).
    ap.add_argument("--group-size", type=int, default=64)
    ap.add_argument("--flush-us", type=int, default=50)
    ap.add_argument("--max-pending", type=int, default=65536)
    args = ap.parse_args()
    out_dir = RESULTS / "dependency_density"
    out_dir.mkdir(parents=True, exist_ok=True)

    # raw[(remote, policy)] = list of metric dicts
    raw = {}
    nz_by_remote = {}
    for repeat in range(args.repeats):
        for remote in REMOTES:
            for policy in POLICY_ORDER:
                r = run_case(out_dir, POLICIES[policy], remote, repeat, args)
                raw.setdefault((remote, policy), []).append(r)
                if policy == "Ayame":  # only dep-frontier collects the frontier
                    nz_by_remote.setdefault(remote, []).append(r["nz_per_tx"])
                print(f"rep{repeat} remote={remote:>8} {policy:14} "
                      f"tps={r['ack_tps']:.0f} p99={r['p99_us']:.0f} "
                      f"pend={r['pending']:.0f} nz/tx={r['nz_per_tx']:.2f}", flush=True)

    csv_path = TABLE_DIR / "dependency_density_20260614.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["remote_ppm", "nz_per_tx", "policy",
                                          "ack_tps", "p99_us", "pending"])
        w.writeheader()
        rows_for_tex = []
        for remote in REMOTES:
            nz = mean(nz_by_remote.get(remote, [0.0]))
            for policy in POLICY_ORDER:
                items = raw[(remote, policy)]
                row = {
                    "remote_ppm": remote,
                    "nz_per_tx": f"{nz:.1f}",
                    "policy": policy,
                    "ack_tps": f"{mean(x['ack_tps'] for x in items):.0f}",
                    "p99_us": f"{median(x['p99_us'] for x in items):.0f}",
                    "pending": f"{mean(x['pending'] for x in items):.0f}",
                }
                w.writerow(row)
                rows_for_tex.append((remote, nz, policy, items))

    # LaTeX table
    lines = [
        "\\begin{table}[t]",
        "  \\centering",
        "  \\caption{Dependency-density experiment at 48 worker threads on an "
        "abort-free partitioned workload.  The remote-access probability sets the "
        "frontier width (nz/tx is the mean number of shards per dependency "
        "frontier).}",
        "  \\label{tab:dep-density}",
        "  \\small",
        "  \\begin{tabular}{rrlrrr}",
        "    \\hline",
        "    Remote & nz/tx & Ack policy & Ack tps & p99 $\\mu$s & Pending \\\\",
        "    \\hline",
    ]
    for remote, nz, policy, items in rows_for_tex:
        lines.append(
            f"    {remote/10000:.0f}\\% & {nz:.1f} & {POLICY_LABEL[policy]} & "
            f"{kfmt(mean(x['ack_tps'] for x in items))} & "
            f"{commafmt(median(x['p99_us'] for x in items))} & "
            f"{commafmt(mean(x['pending'] for x in items))} \\\\")
    lines += ["    \\hline", "  \\end{tabular}", "\\end{table}"]
    tex_path = TABLE_DIR / "table_dependency_density.tex"
    tex_path.write_text("\n".join(lines) + "\n")
    print("CSV:", csv_path)
    print("TEX:", tex_path)


if __name__ == "__main__":
    main()
