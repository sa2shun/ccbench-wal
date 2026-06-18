#!/usr/bin/env python3
"""Scalability breakdown: why Ayame's throughput peaks then declines.

The Ayame throughput curve (Figure for fig:ycsbabc-worker-throughput) rises
to a peak near 24--36 worker threads and then declines.  This experiment
shows the cause is centralized contention in the underlying SSN concurrency
control (the single global commit-timestamp counter and the central
transaction-mapping table), not the abort rate: we measure the per-committed-
transaction CPU cost (cycles/tx and last-level-cache misses/tx) and the abort
rate across the worker-thread sweep.  As threads grow the per-transaction
cost rises sharply (cache-line bouncing on the shared structures) while the
abort rate stays small -- and YCSB-B shows the same decline with a near-zero
abort rate.  All commands use ``numactl --interleave=all`` and the true TSC.
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

WORKERS = [1, 12, 24, 36, 48, 60, 72, 84, 96]
LOGGERS = {1: 1, 12: 3, 24: 5, 36: 7, 48: 9, 60: 12, 72: 14, 84: 17, 96: 19}
WORKLOADS = {"YCSB-A": 50, "YCSB-B": 95}
COLORS = {"YCSB-A": "#b91c1c", "YCSB-B": "#047857"}
EVENTS = ["cycles", "instructions", "LLC-load-misses"]


def run_case(out_dir, worker, rratio, args):
    wal_dir = out_dir / "wal"
    shutil.rmtree(wal_dir, ignore_errors=True)
    wal_dir.mkdir(parents=True, exist_ok=True)
    perf_path = out_dir / "perf.csv"
    env = os.environ.copy()
    env.update({
        "CCBENCH_WAL_DIR": str(wal_dir), "CCBENCH_WAL_SKIP_READ_ONLY": "1",
        "CCBENCH_WAL_MODE": "per_thread",
        "CCBENCH_WAL_DURABLE_MODE": "async_dep_frontier_cstamp",
        "CCBENCH_WAL_LOGGER_NUM": str(LOGGERS.get(worker, max(1, round(worker * 7 / 32)))),
        "CCBENCH_WAL_COMMITTER_NUM": "1", "CCBENCH_WAL_GROUP_SIZE": "256",
        "CCBENCH_WAL_FLUSH_US": "50", "CCBENCH_WAL_MAX_PENDING": "65536",
    })
    cmd = [
        "perf", "stat", "-x,", "-e", ",".join(EVENTS), "-o", str(perf_path), "--",
        "numactl", "--interleave=all",
        str(EXE), f"--thread_num={worker}", f"--extime={args.seconds}",
        "--clocks_per_us=1800", "--ycsb_tuple_num=100000", "--ycsb_max_ope=10",
        f"--ycsb_rratio={rratio}",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    tps = abort = 0.0
    for line in proc.stdout.splitlines():
        m = re.match(r"^throughput\[tps\]:\s*(\d+)", line)
        if m:
            tps = float(m.group(1))
        m = re.match(r"^abort_rate:\s*([\d.]+)", line)
        if m:
            abort = float(m.group(1))
    perf = {}
    for row in csv.reader(perf_path.open()):
        if len(row) >= 3 and row[2] in EVENTS:
            try:
                perf[row[2]] = float(row[0])
            except ValueError:
                pass
    commits = max(tps * args.seconds, 1.0)
    cyc = perf.get("cycles", 0.0)
    return {
        "tps": tps, "abort_pct": abort * 100.0,
        "cycles_per_tx": cyc / commits, "llc_miss_per_tx": perf.get("LLC-load-misses", 0.0) / commits,
        "ipc": perf.get("instructions", 0.0) / max(cyc, 1.0),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--input-csv", default="")
    args = ap.parse_args()
    if args.input_csv:
        rows = list(csv.DictReader(open(args.input_csv)))
        draw(rows, Path(args.input_csv))
        return
    out_dir = RESULTS / "scalability_breakdown"
    out_dir.mkdir(parents=True, exist_ok=True)
    agg = {}
    for rep in range(args.repeats):
        for wl, rratio in WORKLOADS.items():
            for w in WORKERS:
                r = run_case(out_dir, w, rratio, args)
                for k, v in r.items():
                    agg.setdefault((wl, w), {}).setdefault(k, []).append(v)
                print(f"rep{rep} {wl} w{w:>2} cyc/tx={r['cycles_per_tx']:>8,.0f} "
                      f"IPC={r['ipc']:.2f} llc/tx={r['llc_miss_per_tx']:>6.0f} "
                      f"abort={r['abort_pct']:.2f}% tps={r['tps']:.0f}", flush=True)
    csv_path = TABLE_DIR / "scalability_breakdown.csv"
    rows = []
    with csv_path.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["workload", "workers", "cycles_per_tx", "llc_miss_per_tx", "ipc", "abort_pct", "tps"])
        for wl in WORKLOADS:
            for w in WORKERS:
                d = agg[(wl, w)]
                row = {"workload": wl, "workers": w}
                for k in ("cycles_per_tx", "llc_miss_per_tx", "ipc", "abort_pct", "tps"):
                    row[k] = sum(d[k]) / len(d[k])
                wr.writerow([wl, w, f"{row['cycles_per_tx']:.0f}", f"{row['llc_miss_per_tx']:.1f}",
                             f"{row['ipc']:.3f}", f"{row['abort_pct']:.3f}", f"{row['tps']:.0f}"])
                rows.append({k: str(v) for k, v in row.items()})
    draw(rows, csv_path)


def draw(rows, csv_path):
    sns.set_theme(context="paper", style="white", font_scale=1.5, rc={
        "font.family": "DejaVu Sans", "axes.labelsize": 15, "axes.titlesize": 16,
        "xtick.labelsize": 13, "ytick.labelsize": 13, "legend.fontsize": 12,
        "axes.edgecolor": "#9ca3af", "pdf.fonttype": 42, "ps.fonttype": 42})
    plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white",
                         "savefig.facecolor": "white"})
    data = {}
    for r in rows:
        data.setdefault(r["workload"], []).append(
            (int(r["workers"]), float(r["cycles_per_tx"]), float(r["abort_pct"])))
    for wl in data:
        data[wl].sort()
    fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
    ax2 = ax.twinx()
    for wl in WORKLOADS:
        pts = data[wl]
        ax.plot([w for w, _, _ in pts], [c / 1000 for _, c, _ in pts], color=COLORS[wl],
                marker="o", markersize=6, linewidth=2.4, markerfacecolor="white",
                markeredgewidth=1.6, label=f"{wl} cycles/tx")
        ax2.plot([w for w, _, _ in pts], [a for _, _, a in pts], color=COLORS[wl],
                 marker="^", markersize=6, linewidth=1.8, linestyle="--",
                 markerfacecolor="white", markeredgewidth=1.4, label=f"{wl} abort")
    ax.set_xticks(WORKERS)
    ax.set_xticklabels([str(w) for w in WORKERS])
    ax.set_xlabel("Worker threads")
    ax.set_ylabel("CPU cycles per committed tx [K]")
    ax2.set_ylabel("Abort rate [%]")
    ax.set_ylim(bottom=0)
    ax2.set_ylim(bottom=0)
    ax.set_title("Per-transaction cost (solid) vs abort rate (dashed)",
                 fontsize=13, fontweight="bold")
    ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, loc="upper left", ncols=2)
    out = FIG_DIR / "fig_scalability_breakdown.pdf"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print("CSV:", csv_path)
    print("FIG:", out)


if __name__ == "__main__":
    main()
