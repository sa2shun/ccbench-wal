#!/usr/bin/env python3
"""YCSB-A cache-miss ratio vs worker threads.

Investigates the throughput decline at high worker counts (notably 96 on
the two-socket machine) by measuring the last-level cache-miss ratio
(``cache-misses`` / ``cache-references``) as the thread count is swept.
All commands use ``numactl --interleave=all`` and the true TSC
(``--clocks_per_us=1800``), matching the main experiments.
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
WORKERS = [1, 12, 24, 36, 48, 60, 72, 84, 96]
LOGGERS = {12: 3, 24: 5, 36: 7, 48: 9, 60: 12, 72: 14, 84: 17, 96: 19}
COLORS = {"Single WAL": "#4b5563", "P-WAL": "#d97706", "Ayame": "#047857"}
MARKERS = {"Single WAL": "o", "P-WAL": "s", "Ayame": "^"}
# cache-misses / cache-references is the last-level-cache miss ratio.
EVENTS = ["cache-references", "cache-misses", "LLC-loads", "LLC-load-misses"]


def env_for(mode, worker, wal_dir):
    env = os.environ.copy()
    env.update({
        "CCBENCH_WAL_DIR": str(wal_dir), "CCBENCH_WAL_SKIP_READ_ONLY": "1",
        "CCBENCH_WAL_GROUP_SIZE": "256", "CCBENCH_WAL_FLUSH_US": "50",
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


def parse_perf(text):
    """perf stat -x, lines look like: value,,event-name,run,pct,..."""
    out = {}
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        val, event = parts[0], parts[2]
        if event in EVENTS:
            try:
                out[event] = float(val)
            except ValueError:
                out[event] = float("nan")
    return out


def run_case(out_dir, mode, worker, args):
    wal_dir = out_dir / "wal"
    shutil.rmtree(wal_dir, ignore_errors=True)
    wal_dir.mkdir(parents=True, exist_ok=True)
    perf_path = out_dir / "perf" / f"{mode}_w{worker}.csv"
    perf_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "perf", "stat", "-x,", "-e", ",".join(EVENTS), "-o", str(perf_path), "--",
        "numactl", "--interleave=all",
        str(EXE), f"--thread_num={worker}", f"--extime={args.seconds}",
        "--clocks_per_us=1800", "--ycsb_tuple_num=100000", "--ycsb_max_ope=10",
        "--ycsb_rratio=50",
    ]
    subprocess.run("sync; sleep 3", shell=True)  # rest storage: avoid cumulative-load p99 spikes
    proc = subprocess.run(cmd, cwd=ROOT, env=env_for(mode, worker, wal_dir),
                          capture_output=True, text=True)
    tps = 0.0
    for line in proc.stdout.splitlines():
        m = re.match(r"^throughput\[tps\]:\s*(\d+)", line)
        if m:
            tps = float(m.group(1))
    perf = parse_perf(perf_path.read_text(errors="replace"))
    refs = perf.get("cache-references", float("nan"))
    miss = perf.get("cache-misses", float("nan"))
    llc = perf.get("LLC-loads", float("nan"))
    llcm = perf.get("LLC-load-misses", float("nan"))
    ratio = 100.0 * miss / refs if refs else float("nan")
    llc_ratio = 100.0 * llcm / llc if llc else float("nan")
    return tps, ratio, llc_ratio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--input-csv", default="",
                    help="re-draw the figure from an existing CSV without re-measuring")
    args = ap.parse_args()

    if args.input_csv:
        rows = []
        with open(args.input_csv, newline="") as f:
            for r in csv.DictReader(f):
                rows.append((r["system"], int(r["workers"]),
                             float(r["cache_miss_ratio_pct"]),
                             float(r["llc_load_miss_ratio_pct"]), float(r["tps_mean"])))
        draw(rows, Path(args.input_csv))
        return

    out_dir = RESULTS / "ycsba_cache_miss"
    out_dir.mkdir(parents=True, exist_ok=True)

    agg = {}  # (sysname, worker) -> {"ratio":[], "llc":[], "tps":[]}
    for rep in range(args.repeats):
        for mode, sysname in SYSTEMS.items():
            for w in WORKERS:
                tps, ratio, llc = run_case(out_dir, mode, w, args)
                d = agg.setdefault((sysname, w), {"ratio": [], "llc": [], "tps": []})
                d["ratio"].append(ratio)
                d["llc"].append(llc)
                d["tps"].append(tps)
                print(f"rep{rep} {sysname:10} w{w:>2} cache-miss={ratio:5.1f}% "
                      f"LLC-miss={llc:5.1f}% tps={tps:.0f}", flush=True)

    csv_path = TABLE_DIR / "ycsba_cache_miss.csv"
    rows = []
    with csv_path.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["system", "workers", "cache_miss_ratio_pct", "llc_load_miss_ratio_pct", "tps_mean"])
        for sysname in SYSTEM_ORDER:
            for w in WORKERS:
                d = agg[(sysname, w)]
                cm = sum(d["ratio"]) / len(d["ratio"])
                lm = sum(d["llc"]) / len(d["llc"])
                tp = sum(d["tps"]) / len(d["tps"])
                wr.writerow([sysname, w, f"{cm:.2f}", f"{lm:.2f}", f"{tp:.0f}"])
                rows.append((sysname, w, cm, lm, tp))
    draw(rows, csv_path)


def draw(rows, csv_path):
    sns.set_theme(context="paper", style="white", font_scale=1.5, rc={
        "font.family": "DejaVu Sans", "axes.labelsize": 15, "axes.titlesize": 16,
        "xtick.labelsize": 13, "ytick.labelsize": 13, "legend.fontsize": 13,
        "axes.edgecolor": "#9ca3af", "pdf.fonttype": 42, "ps.fonttype": 42})
    plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white",
                         "savefig.facecolor": "white"})
    data = {}
    for sysname, w, cm, lm, tp in rows:
        data.setdefault(sysname, []).append((w, cm))
    fig, ax = plt.subplots(figsize=(6.4, 4.4), constrained_layout=True)
    for sysname in SYSTEM_ORDER:
        pts = sorted(data[sysname])
        ax.plot([w for w, _ in pts], [c for _, c in pts], color=COLORS[sysname],
                marker=MARKERS[sysname], markersize=7, linewidth=2.4,
                markerfacecolor="white", markeredgewidth=1.6, label=sysname)
    ax.set_xscale("linear")
    ax.minorticks_off()
    ax.set_xticks([1, 12, 24, 36, 48, 60, 72, 84, 96])
    ax.set_xticklabels(["1", "12", "24", "36", "48", "60", "72", "84", "96"])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: str(int(v))))
    ax.set_xlabel("Worker threads")
    ax.set_ylabel("Cache-miss ratio [%]")
    ax.set_title("YCSB-A cache-miss ratio (cache-misses / cache-references)",
                 fontsize=14, fontweight="bold")
    ax.grid(True, which="major", color="#e5e7eb", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False, loc="best")
    out = FIG_DIR / "fig_ycsba_cache_miss.pdf"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print("CSV:", csv_path)
    print("FIG:", out)


if __name__ == "__main__":
    main()
