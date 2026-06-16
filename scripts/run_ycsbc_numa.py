#!/usr/bin/env python3
"""YCSB-C NUMA scaling: default vs numactl --interleave=all.

YCSB-C is read-only (no WAL records), so this isolates the read/CC path.
We sweep worker threads to 96 (the full two-socket machine) and compare
default first-touch memory placement against interleaved placement.
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

SYSTEMS = {"single_wal": "Single WAL", "pwal": "P-WAL", "tidewal": "Ayame"}
SYSTEM_ORDER = ["Single WAL", "P-WAL", "Ayame"]
WORKERS = [1, 2, 4, 8, 16, 32, 48, 96]
LOGGERS = {1: 1, 2: 1, 4: 1, 8: 2, 16: 4, 32: 7, 48: 9, 96: 19}
CONDITIONS = ["default", "interleave"]
COLORS = {"Single WAL": "#4b5563", "P-WAL": "#d97706", "Ayame": "#047857"}
MARKERS = {"Single WAL": "o", "P-WAL": "s", "Ayame": "^"}


def env_for(mode, worker, wal_dir):
    env = os.environ.copy()
    env.update({
        "CCBENCH_WAL_DIR": str(wal_dir), "CCBENCH_WAL_SKIP_READ_ONLY": "1",
        "CCBENCH_WAL_GROUP_SIZE": "16", "CCBENCH_WAL_FLUSH_US": "50",
        "CCBENCH_WAL_MAX_PENDING": "65536", "CCBENCH_WAL_COMMITTER_NUM": "1",
    })
    log = LOGGERS.get(worker, max(1, round(worker * 7 / 32)))
    if mode == "single_wal":
        env.update({"CCBENCH_WAL_MODE": "shared", "CCBENCH_WAL_DURABLE_MODE": "sync",
                    "CCBENCH_WAL_LOGGER_NUM": str(worker)})
    elif mode == "pwal":
        env.update({"CCBENCH_WAL_MODE": "per_thread", "CCBENCH_WAL_DURABLE_MODE": "sync",
                    "CCBENCH_WAL_LOGGER_NUM": str(worker)})
    else:
        env.update({"CCBENCH_WAL_MODE": "per_thread",
                    "CCBENCH_WAL_DURABLE_MODE": "async_dep_frontier_cstamp",
                    "CCBENCH_WAL_LOGGER_NUM": str(log)})
    return env


def run_case(out_dir, mode, worker, condition, args):
    wal_dir = out_dir / "wal"
    # Shared across all runs; clear before each so WAL files do not accumulate.
    shutil.rmtree(wal_dir, ignore_errors=True)
    wal_dir.mkdir(parents=True, exist_ok=True)
    bench = [str(EXE), f"--thread_num={worker}", f"--extime={args.seconds}",
             "--clocks_per_us=1800", "--ycsb_tuple_num=100000",
             "--ycsb_max_ope=10", "--ycsb_rratio=100"]
    cmd = (["numactl", "--interleave=all"] + bench) if condition == "interleave" else bench
    proc = subprocess.run(cmd, cwd=ROOT, env=env_for(mode, worker, wal_dir),
                          capture_output=True, text=True)
    tps = 0.0
    for line in proc.stdout.splitlines():
        m = re.match(r"^throughput\[tps\]:\s*(\d+)", line)
        if m:
            tps = float(m.group(1))
    return tps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=3)
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()
    out_dir = RESULTS / "ycsbc_numa"
    out_dir.mkdir(parents=True, exist_ok=True)

    agg = {}  # (system, condition, worker) -> [tps...]
    for rep in range(args.repeats):
        for mode, sysname in SYSTEMS.items():
            for cond in CONDITIONS:
                for w in WORKERS:
                    tps = run_case(out_dir, mode, w, cond, args)
                    agg.setdefault((sysname, cond, w), []).append(tps)
                    print(f"rep{rep} {sysname:10} {cond:10} w{w:>2} tps={tps:.0f}", flush=True)

    csv_path = TABLE_DIR / "ycsbc_numa.csv"
    rows = []
    with csv_path.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["system", "condition", "workers", "tps_mean"])
        for sysname in SYSTEM_ORDER:
            for cond in CONDITIONS:
                for w in WORKERS:
                    vals = agg[(sysname, cond, w)]
                    m = sum(vals) / len(vals)
                    wr.writerow([sysname, cond, w, f"{m:.0f}"])
                    rows.append((sysname, cond, w, m))

    draw(rows, csv_path)


def draw(rows, csv_path):
    sns.set_theme(context="paper", style="white", font_scale=1.5, rc={
        "font.family": "DejaVu Sans", "axes.labelsize": 15, "axes.titlesize": 16,
        "xtick.labelsize": 13, "ytick.labelsize": 13, "legend.fontsize": 13,
        "axes.edgecolor": "#9ca3af", "pdf.fonttype": 42, "ps.fonttype": 42})
    plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white",
                         "savefig.facecolor": "white"})
    data = {}
    for sysname, cond, w, m in rows:
        data.setdefault((cond, sysname), []).append((w, m))
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.2), sharey=True,
                             constrained_layout=True)
    for ax, cond in zip(axes, CONDITIONS):
        # ideal-linear reference from the Ayame 1-thread point
        base = next(m for s, c, w, m in rows if c == cond and s == "Ayame" and w == 1)
        ax.plot(WORKERS, [base * w for w in WORKERS], color="#9ca3af",
                linestyle=":", linewidth=1.8, label="ideal linear")
        for sysname in SYSTEM_ORDER:
            pts = sorted(data[(cond, sysname)])
            ax.plot([w for w, _ in pts], [m for _, m in pts], color=COLORS[sysname],
                    marker=MARKERS[sysname], markersize=6.5, linewidth=2.4,
                    markerfacecolor="white", markeredgewidth=1.6, label=sysname)
        ax.set_xscale("log", base=2)
        ax.set_yscale("log", base=10)
        ax.set_xticks(WORKERS)
        ax.set_xticklabels([str(w) for w in WORKERS])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: str(int(v))))
        ax.set_title("Default" if cond == "default" else "interleave=all",
                     fontsize=16, fontweight="bold")
        ax.set_xlabel("Worker threads")
        ax.grid(True, which="major", color="#e5e7eb", linewidth=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    axes[0].set_ylabel("YCSB-C throughput [tx/s]")
    axes[1].legend(frameon=False, loc="lower right")
    out = FIG_DIR / "fig_ycsbc_numa.pdf"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print("CSV:", csv_path)
    print("FIG:", out)


if __name__ == "__main__":
    main()
