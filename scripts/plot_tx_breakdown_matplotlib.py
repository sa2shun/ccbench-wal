#!/usr/bin/env python3
import csv
import os
import sys
from pathlib import Path

# Allow the locally unpacked matplotlib debs used on machines without sudo/pip.
ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local-debs" / "matplotlib"
LOCAL_SITE = LOCAL / "usr" / "lib" / "python3" / "dist-packages"
LOCAL_LIB = LOCAL / "usr" / "lib" / "x86_64-linux-gnu"
LOCAL_BLAS = LOCAL_LIB / "blas"
LOCAL_LAPACK = LOCAL_LIB / "lapack"
LOCAL_RC = LOCAL / "usr" / "share" / "matplotlib" / "mpl-data" / "matplotlibrc"
if LOCAL_SITE.exists():
    sys.path.insert(0, str(LOCAL_SITE))
    libs = [str(LOCAL_LIB), str(LOCAL_BLAS), str(LOCAL_LAPACK)]
    os.environ["LD_LIBRARY_PATH"] = ":".join(libs + [os.environ.get("LD_LIBRARY_PATH", "")])
    os.environ.setdefault("MATPLOTLIBRC", str(LOCAL_RC))
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".local-debs" / "mplconfig"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

THREADS = [1, 2, 4, 8, 16, 32]
EXPERIMENTS = [("normal_ycsb", "Normal YCSB"), ("abort0_ycsb", "Abort-0 YCSB")]
MODES = [("ermia_wal", "ERMIA+WAL", "#2f6f9f"), ("ermia_pwal", "ERMIA+P-WAL", "#d97b32")]
TOP_STACKS = [
    ("group_ermia_other_ns_pct", "ERMIA other", "#4c78a8"),
    ("group_ssn_ns_pct", "SSN", "#f58518"),
    ("group_wal_ns_pct", "WAL", "#54a24b"),
    ("group_unaccounted_ns_pct", "unaccounted", "#cfd7e2"),
]
WAL_STACKS = [
    ("wal_payload_build_ns_pct", "payload build", "#6a9fb5"),
    ("wal_mutex_wait_ns_pct", "mutex wait", "#d95f02"),
    ("wal_write_ns_pct", "write", "#7570b3"),
    ("wal_fdatasync_ns_pct", "fdatasync", "#1b9e77"),
    ("wal_notify_wait_ns_pct", "notify wait", "#e7298a"),
]


def load(path):
    lines = path.read_text().splitlines()
    return list(csv.DictReader(lines[lines.index("===== SUMMARY_CSV =====") + 1:]))


def row_for(data, exp, mode, th):
    return next(r for r in data if r["experiment"] == exp and r["mode"] == mode and int(r["thread_num"]) == th)


def num(row, key):
    try:
        return float(row.get(key, 0) or 0)
    except Exception:
        return 0.0


def fmt_k(x, _):
    if x >= 1000:
        return f"{x/1000:.0f}k"
    return f"{x:.0f}"


def plot_throughput(ax, data, exp, title):
    for mode, label, color in MODES:
        ys = [num(row_for(data, exp, mode, th), "throughput_tps") for th in THREADS]
        ax.plot(THREADS, ys, marker="o", markersize=6.5, linewidth=2.4,
                label=label, color=color)
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold")
    ax.set_xscale("log", base=2)
    ax.set_xticks(THREADS)
    ax.set_xticklabels([str(t) for t in THREADS])
    ax.set_xlabel("threads")
    ax.set_ylabel("throughput [tps]")
    ax.yaxis.set_major_formatter(FuncFormatter(fmt_k))
    ax.grid(True, axis="both", color="#e7ebef", linewidth=0.8)
    ax.legend(frameon=False, loc="upper left", fontsize=9)


def annotate_segment(ax, left, width, y, label):
    if width >= 7:
        ax.text(left + width / 2, y, label, va="center", ha="center",
                color="white", fontsize=8, fontweight="bold")


def stacked_bars(ax, data, exp, title, stacks):
    y_positions = [1, 0]
    labels = []
    for y, (mode, label, _) in zip(y_positions, MODES):
        row = row_for(data, exp, mode, 32)
        labels.append(label)
        left = 0.0
        for key, stack_label, color in stacks:
            val = max(0.0, min(100.0, num(row, key)))
            ax.barh(y, val, left=left, color=color, edgecolor="white", height=0.45)
            annotate_segment(ax, left, val, y, f"{val:.0f}%")
            left += val
        tps = num(row, "throughput_tps")
        abort = num(row, "abort_rate")
        ax.text(103, y + 0.07, f"{tps:.0f} tps", fontsize=9, fontweight="bold", va="center")
        ax.text(103, y - 0.13, f"abort {abort:.4f}", fontsize=8, color="#5d6a78", va="center")
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold")
    ax.set_xlim(0, 128)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels)
    ax.set_xlabel("share of total thread-time [%]")
    ax.grid(True, axis="x", color="#e7ebef", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=color) for _, _, color in stacks]
    ax.legend(handles, [label for _, label, _ in stacks], frameon=False,
              ncol=min(len(stacks), 4), fontsize=8, loc="lower left",
              bbox_to_anchor=(0, -0.48))


def main():
    if len(sys.argv) != 2:
        print("usage: plot_tx_breakdown_matplotlib.py RESULT_TXT", file=sys.stderr)
        raise SystemExit(2)
    path = Path(sys.argv[1])
    data = load(path)
    out_svg = path.with_name(path.stem + "_matplotlib.svg")
    out_png = path.with_name(path.stem + "_matplotlib.png")

    plt.rcParams.update({
        "figure.facecolor": "#f7f9fb",
        "axes.facecolor": "white",
        "axes.edgecolor": "#c8d0d9",
        "font.size": 10,
        "savefig.bbox": "tight",
    })
    fig, axes = plt.subplots(3, 2, figsize=(15.5, 10.5), constrained_layout=True)
    fig.suptitle("ERMIA+WAL vs ERMIA+P-WAL: Hierarchical Time Breakdown",
                 fontsize=18, fontweight="bold", x=0.01, ha="left")
    fig.text(0.01, 0.965,
             "Top: throughput scaling. Middle: total time at 32 threads. Bottom: WAL internal time at 32 threads.",
             fontsize=10, color="#52606d", ha="left")

    for col, (exp, label) in enumerate(EXPERIMENTS):
        plot_throughput(axes[0, col], data, exp, f"1. Throughput: {label}")
        stacked_bars(axes[1, col], data, exp, f"2. Total breakdown at 32 threads: {label}", TOP_STACKS)
        stacked_bars(axes[2, col], data, exp, f"3. WAL internal breakdown at 32 threads: {label}", WAL_STACKS)

    fig.text(0.01, 0.01,
             "Takeaway: shared WAL is dominated by mutex wait; P-WAL removes that bottleneck and shifts cost to fdatasync/notification wait.",
             fontsize=10, color="#52606d", ha="left")
    fig.savefig(out_svg)
    fig.savefig(out_png, dpi=180)
    print(out_svg)
    print(out_png)


if __name__ == "__main__":
    main()
