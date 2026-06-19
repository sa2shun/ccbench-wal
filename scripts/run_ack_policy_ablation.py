#!/usr/bin/env python3
"""Acknowledgment-policy ablation.

Both policies use the identical asynchronous WAL pipeline (same per-shard
flushers and one committer); they differ only in the durable-acknowledgment
condition:

  global_prefix : acknowledge once the global durable prefix passes the
                  transaction's LSN (the P-WAL rule).
  dep_frontier  : acknowledge once the shard positions in the transaction's
                  dependency frontier are durable (Ayame's rule).

This isolates the effect of the acknowledgment condition (A3) without the
synchronous-vs-asynchronous confound and without any injected straggler.
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "build" / "cc" / "ermia_pwal" / "ycsb_ermia_pwal.exe"
FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"
RESULTS = ROOT / "results"

POLICIES = {
    "Global-prefix": "async_global_lsn_prefix",
    "Ayame": "async_dep_frontier_cstamp",
}
POLICY_ORDER = ["Global-prefix", "Ayame"]
WORKLOADS = [("YCSB-A", 50), ("YCSB-B", 95)]
LOGGERS = {1: 1, 2: 1, 4: 1, 8: 2, 16: 4, 32: 7}
COLORS = {"Global-prefix": "#d97706", "Ayame": "#047857"}
MARKERS = {"Global-prefix": "s", "Ayame": "^"}


def loggers(worker):
    return LOGGERS.get(worker, max(1, round(worker * 7 / 32)))


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        m = re.match(r"^#?([A-Za-z0-9_\[\]]+):\s*(.*)$", line)
        if m:
            row[m.group(1)] = m.group(2).strip()
    return row


def run_case(out_dir, policy, mode_value, workload, rratio, worker, repeat, args):
    wal_dir = out_dir / "wal"
    # Shared across all runs; clear before each so WAL files do not accumulate.
    shutil.rmtree(wal_dir, ignore_errors=True)
    wal_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "CCBENCH_WAL_DIR": str(wal_dir),
        "CCBENCH_WAL_SKIP_READ_ONLY": "1",
        "CCBENCH_WAL_MODE": "per_thread",
        "CCBENCH_WAL_DURABLE_MODE": mode_value,
        "CCBENCH_WAL_LOGGER_NUM": str(loggers(worker)),
        "CCBENCH_WAL_COMMITTER_NUM": "1",
        "CCBENCH_WAL_GROUP_SIZE": str(args.group_size),
        "CCBENCH_WAL_FLUSH_US": str(args.flush_us),
        "CCBENCH_WAL_MAX_PENDING": str(args.max_pending),
    })
    cmd = [
        "numactl", "--interleave=all",
        str(EXE), f"--thread_num={worker}", f"--extime={args.seconds}",
        "--clocks_per_us=1800", "--ycsb_tuple_num=100000",
        "--ycsb_max_ope=10", f"--ycsb_rratio={rratio}",
    ]
    subprocess.run("sync; sleep 3", shell=True)  # rest storage: avoid cumulative-load p99 spikes
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    m = parse_metrics(proc.stdout)
    acked = float(m.get("wal_stats_async_acked_commits", 0) or 0)
    ext = float(m.get("actual_extime", 0) or args.seconds)
    return {
        "policy": policy,
        "workload": workload,
        "worker_threads": worker,
        "repeat": repeat,
        "ack_tps": acked / ext if ext else 0.0,
        "p99_us": float(m.get("wal_stats_ack_latency_p99_us", 0) or 0),
        # Snapshot pending at the measurement stop, matching the main summary
        # table (not the transient peak max_pending_commits).
        "pending": float(m.get("wal_stats_measurement_pending_commits", 0) or 0),
    }


def aggregate(rows):
    keys = {}
    for r in rows:
        keys.setdefault((r["policy"], r["workload"], r["worker_threads"]), []).append(r)
    out = []
    for (policy, workload, worker), items in keys.items():
        def med(field):
            vals = sorted(x[field] for x in items)
            n = len(vals)
            return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        out.append({
            "policy": policy, "workload": workload, "worker_threads": worker,
            "ack_tps": sum(x["ack_tps"] for x in items) / len(items),
            "p99_us": med("p99_us"),
            "pending": sum(x["pending"] for x in items) / len(items),
        })
    return out


def setup_style():
    sns.set_theme(context="paper", style="white", font_scale=1.55, rc={
        "font.family": "DejaVu Sans", "axes.labelsize": 15, "axes.titlesize": 16,
        "xtick.labelsize": 13, "ytick.labelsize": 13, "legend.fontsize": 14,
        "axes.edgecolor": "#9ca3af", "axes.linewidth": 0.9,
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })
    plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white",
                         "savefig.facecolor": "white"})


def compact(v):
    if v >= 1_000_000:
        return f"{v/1e6:.0f}M"
    if v >= 1000:
        return f"{v/1000:.0f}K"
    return f"{v:.0f}"


def draw(rows, workers, out_path):
    setup_style()
    fig, axes = plt.subplots(2, 2, figsize=(6.6, 4.6), constrained_layout=True)
    panels = [("p99_us", "p99 latency [us]", "log"),
              ("pending", "Pending durable commits", "symlog")]
    for col, (workload, _) in enumerate(WORKLOADS):
        for row_i, (metric, ylabel, yscale) in enumerate(panels):
            ax = axes[row_i][col]
            for policy in POLICY_ORDER:
                pts = sorted((r for r in rows if r["policy"] == policy and r["workload"] == workload),
                             key=lambda r: r["worker_threads"])
                ax.plot([p["worker_threads"] for p in pts], [p[metric] for p in pts],
                        color=COLORS[policy], marker=MARKERS[policy], linewidth=2.6,
                        markersize=7, markerfacecolor="white", markeredgewidth=1.8, label=policy)
            if yscale != "linear":
                ax.set_yscale(yscale)
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: compact(v)))
            ax.set_xscale("linear")
            ax.set_xticks([1, 48, 96])
            ax.minorticks_off()
            ax.set_xticklabels(["1", "48", "96"])
            if row_i == 0:
                ax.set_title(workload, fontsize=14, fontweight="bold")
            if row_i == 1:
                ax.set_xlabel("Worker threads")
            if col == 0:
                ax.set_ylabel(ylabel)
            ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.85)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
    handles = [plt.Line2D([0], [0], color=COLORS[p], marker=MARKERS[p], markerfacecolor="white",
                          markeredgewidth=1.8, linewidth=2.4, label=p) for p in POLICY_ORDER]
    fig.legend(handles=handles, ncols=2, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 1.14))
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", default="1,12,24,36,48,60,72,84,96")
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--group-size", type=int, default=256)
    ap.add_argument("--flush-us", type=int, default=50)
    ap.add_argument("--max-pending", type=int, default=65536)
    ap.add_argument("--input-csv", default="",
                    help="re-draw the figure from an existing CSV without re-measuring")
    args = ap.parse_args()
    workers = [int(x) for x in args.workers.split(",") if x]

    if args.input_csv:
        agg = []
        with open(args.input_csv, newline="") as f:
            for r in csv.DictReader(f):
                agg.append({"policy": r["policy"], "workload": r["workload"],
                            "worker_threads": int(r["worker_threads"]),
                            "ack_tps": float(r["ack_tps"]), "p99_us": float(r["p99_us"]),
                            "pending": float(r["pending"])})
        workers = sorted({a["worker_threads"] for a in agg})
        fig_path = FIG_DIR / "fig_ack_policy_ablation.pdf"
        draw(agg, workers, fig_path)
        print("FIG (replot):", fig_path)
        return

    out_dir = RESULTS / "ack_policy_ablation"
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = []
    for repeat in range(args.repeats):
        for policy in POLICY_ORDER:
            mode_value = POLICIES[policy]
            for workload, rratio in WORKLOADS:
                for worker in workers:
                    r = run_case(out_dir, policy, mode_value, workload, rratio, worker, repeat, args)
                    raw.append(r)
                    print(f"rep{repeat} {policy:14} {workload} w{worker:>2} "
                          f"tps={r['ack_tps']:.0f} p99={r['p99_us']:.0f} pend={r['pending']:.0f}",
                          flush=True)

    agg = aggregate(raw)
    csv_path = TABLE_DIR / "ack_policy_ablation_20260614.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["policy", "workload", "worker_threads",
                                          "ack_tps", "p99_us", "pending"])
        w.writeheader()
        for r in sorted(agg, key=lambda r: (r["workload"], POLICY_ORDER.index(r["policy"]),
                                            r["worker_threads"])):
            w.writerow(r)
    fig_path = FIG_DIR / "fig_ack_policy_ablation.pdf"
    draw(agg, workers, fig_path)
    print("CSV:", csv_path)
    print("FIG:", fig_path)


if __name__ == "__main__":
    main()
