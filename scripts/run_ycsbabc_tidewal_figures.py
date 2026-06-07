#!/usr/bin/env python3
import argparse
import csv
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.ticker import FuncFormatter

from run_ermia_cstamp_pwal_experiments import (
    PWAL_YCSB_EXE,
    RESULTS,
    ROOT,
    WORKLOAD_PRESETS,
    derived,
    fnum,
    write_csv,
)


FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"
WAL_YCSB_EXE = ROOT / "build" / "cc" / "ermia_wal" / "ycsb_ermia_wal.exe"
OUT_CSV = TABLE_DIR / "ycsbabc_tidewal_paper_figures_20260607.csv"
B_SUMMARY_CSV = TABLE_DIR / "ycsbb_tidewal_paper_figures_20260607.csv"
B_TIDEWAL_CSV = TABLE_DIR / "ycsbb_cstamp_readonly_skip_20260607.csv"

MODES = [
    "ermia_single_wal",
    "ermia_plain_pwal",
    "ermia_async_dep_frontier_cstamp",
]

LABELS = {
    "ermia_single_wal": "Single WAL",
    "ermia_plain_pwal": "P-WAL",
    "ermia_async_dep_frontier_cstamp": "TideWAL",
}

SYSTEM_ORDER = ["Single WAL", "P-WAL", "TideWAL"]

WORKLOAD_LABELS = {
    "ycsb_a": "YCSB-A",
    "ycsb_b": "YCSB-B",
    "ycsb_c": "YCSB-C",
}

WORKLOAD_CAPTIONS = {
    "ycsb_a": "YCSB-A, 50% reads, 10 operations per transaction",
    "ycsb_b": "YCSB-B, 95% reads, 10 operations per transaction",
    "ycsb_c": "YCSB-C, 100% reads, 10 operations per transaction",
}

THREADS = [1, 2, 4, 8, 16, 32]

COLORS = {
    "Single WAL": "#4b5563",
    "P-WAL": "#d97706",
    "TideWAL": "#047857",
}

WORKLOAD_COLORS = {
    "YCSB-A": "#be123c",
    "YCSB-B": "#047857",
    "YCSB-C": "#2563eb",
}

MARKERS = {
    "Single WAL": "o",
    "P-WAL": "s",
    "TideWAL": "^",
    "YCSB-A": "o",
    "YCSB-B": "s",
    "YCSB-C": "^",
}


def parse_int_list(text):
    return [int(x) for x in text.split(",") if x]


def parse_workloads(text):
    workloads = [x for x in text.split(",") if x]
    unknown = [x for x in workloads if x not in WORKLOAD_PRESETS]
    if unknown:
        raise ValueError(f"unknown workload(s): {', '.join(unknown)}")
    return workloads


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        match = re.match(r"^#?([A-Za-z0-9_]+):\s*(.*)$", line)
        if match:
            row[match.group(1)] = match.group(2).strip()
    return row


def read_rows(path):
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


def safe_float(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def median(values):
    values = sorted(values)
    if not values:
        return 0.0
    n = len(values)
    if n % 2:
        return values[n // 2]
    return (values[n // 2 - 1] + values[n // 2]) / 2.0


def compact_number(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1000:
        return f"{v / 1000:.0f}K"
    if 0 < v < 10:
        return f"{v:.1f}"
    return f"{v:.0f}"


def run_case(out_dir, workload, mode, repeat, thread_num, args):
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir = out_dir / "wal"
    preset = WORKLOAD_PRESETS[workload]

    env = os.environ.copy()
    env["CCBENCH_WAL_DIR"] = str(wal_dir)
    if mode == "ermia_single_wal":
        exe = WAL_YCSB_EXE
        logger_num = thread_num
        committer_num = 0
        group_size = 0
        flush_us = 0
    else:
        exe = PWAL_YCSB_EXE
        if mode == "ermia_plain_pwal":
            env["CCBENCH_WAL_DURABLE_MODE"] = "sync"
            logger_num = thread_num
            committer_num = 0
            group_size = 0
            flush_us = 0
        elif mode == "ermia_async_dep_frontier_cstamp":
            env["CCBENCH_WAL_DURABLE_MODE"] = "async_dep_frontier_cstamp"
            logger_num = args.logger_num
            committer_num = args.committer_num
            group_size = args.group_size
            flush_us = args.flush_us
        else:
            raise ValueError(f"unknown mode: {mode}")
        env["CCBENCH_WAL_LOGGER_NUM"] = str(logger_num)
        env["CCBENCH_WAL_COMMITTER_NUM"] = str(committer_num)
        env["CCBENCH_WAL_GROUP_SIZE"] = str(group_size)
        env["CCBENCH_WAL_FLUSH_US"] = str(flush_us)
        env["CCBENCH_WAL_MAX_PENDING"] = str(args.max_pending)

    cmd = [
        str(exe),
        f"--thread_num={thread_num}",
        f"--extime={args.seconds}",
        "--ycsb_tuple_num=100000",
        f"--ycsb_max_ope={preset['ycsb_max_ope']}",
        f"--ycsb_rratio={preset['ycsb_rratio']}",
    ]
    label = f"{workload}_{mode}_th{thread_num}_r{repeat}_l{logger_num}_c{committer_num}"
    out_path = logs / f"{label}.out"
    err_path = logs / f"{label}.err"
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)

    row = parse_metrics(out_path.read_text(errors="replace"))
    row.update(
        {
            "mode": mode,
            "system": LABELS[mode],
            "workload_mode": workload,
            "workload": WORKLOAD_LABELS[workload],
            "repeat": str(repeat),
            "thread_num": str(thread_num),
            "worker_threads": str(thread_num),
            "seconds": str(args.seconds),
            "logger_num": str(logger_num),
            "committer_num": str(committer_num),
            "group_size": str(group_size),
            "flush_us": str(flush_us),
            "max_pending": str(args.max_pending if mode == "ermia_async_dep_frontier_cstamp" else 0),
            "exit_code": str(proc.returncode),
            "stdout_log": str(out_path),
            "stderr_log": str(err_path),
        }
    )
    row = derived(row)
    if safe_float(row.get("durable_ack_tps")) == 0:
        row["durable_ack_tps"] = row.get("throughput[tps]", "0")
    return row


def aggregate_raw_rows(rows):
    grouped = {}
    for row in rows:
        key = (row["workload_mode"], row["system"], int(row["thread_num"]))
        grouped.setdefault(key, []).append(row)
    out = []
    for (workload, system, thread_num), rs in sorted(grouped.items()):
        ack_values = [safe_float(r.get("durable_ack_tps")) for r in rs]
        p99_values = []
        for r in rs:
            p99 = safe_float(r.get("ack_latency_p99_us"))
            if p99 <= 0:
                p99 = thread_num * 1_000_000.0 / max(safe_float(r.get("durable_ack_tps")), 1.0)
            p99_values.append(p99)
        out.append(
            {
                "workload_mode": workload,
                "workload": WORKLOAD_LABELS[workload],
                "system": system,
                "thread_num": thread_num,
                "ack_tps": mean(ack_values),
                "p99_us": median(p99_values),
                "pending": mean(safe_float(r.get("pending_commits")) for r in rs),
                "frontier_collect_ns_per_tx": mean(safe_float(r.get("frontier_collect_ns_per_tx")) for r in rs),
                "frontier_publish_ns_per_tx": mean(safe_float(r.get("frontier_publish_ns_per_tx")) for r in rs),
                "fdatasync_ns_per_tx": mean(safe_float(r.get("fdatasync_ns_per_tx")) for r in rs),
                "read_frontier_updates_per_tx": mean(safe_float(r.get("read_frontier_updates_per_tx")) for r in rs),
                "write_frontier_updates_per_tx": mean(safe_float(r.get("write_frontier_updates_per_tx")) for r in rs),
            }
        )
    return out


def load_ycsbb_summary():
    rows = []
    if not B_SUMMARY_CSV.exists():
        return rows
    for row in read_rows(B_SUMMARY_CSV):
        rows.append(
            {
                "workload_mode": "ycsb_b",
                "workload": "YCSB-B",
                "system": row["system"],
                "thread_num": int(row["thread_num"]),
                "ack_tps": safe_float(row["ack_tps"]),
                "p99_us": safe_float(row["p99_us"]),
                "pending": safe_float(row["pending"]),
                "frontier_collect_ns_per_tx": 0.0,
                "frontier_publish_ns_per_tx": 0.0,
                "fdatasync_ns_per_tx": 0.0,
                "read_frontier_updates_per_tx": 0.0,
                "write_frontier_updates_per_tx": 0.0,
            }
        )
    if B_TIDEWAL_CSV.exists():
        for row in read_rows(B_TIDEWAL_CSV):
            if row.get("config") != "tuned_l4_c1_g16_f50":
                continue
            thread_num = int(row["thread_num"])
            for out in rows:
                if out["workload_mode"] == "ycsb_b" and out["system"] == "TideWAL" and out["thread_num"] == thread_num:
                    out["frontier_collect_ns_per_tx"] = safe_float(row.get("frontier_collect_ns_per_tx"))
                    out["frontier_publish_ns_per_tx"] = safe_float(row.get("frontier_publish_ns_per_tx"))
                    out["fdatasync_ns_per_tx"] = safe_float(row.get("fdatasync_ns_per_tx"))
                    out["read_frontier_updates_per_tx"] = safe_float(row.get("read_frontier_updates_per_tx"))
                    out["write_frontier_updates_per_tx"] = safe_float(row.get("write_frontier_updates_per_tx"))
    return rows


def write_plot_csv(rows):
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "workload_mode",
        "workload",
        "system",
        "thread_num",
        "ack_tps",
        "p99_us",
        "pending",
        "frontier_collect_ns_per_tx",
        "frontier_publish_ns_per_tx",
        "fdatasync_ns_per_tx",
        "read_frontier_updates_per_tx",
        "write_frontier_updates_per_tx",
    ]
    with OUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def setup_style():
    sns.set_theme(
        context="paper",
        style="white",
        font_scale=1.18,
        rc={
            "font.family": "DejaVu Sans",
            "axes.labelcolor": "#111827",
            "xtick.color": "#374151",
            "ytick.color": "#374151",
            "axes.edgecolor": "#9ca3af",
            "axes.linewidth": 0.9,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        },
    )
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def decorate_axis(ax, ylabel, yfmt=None, yscale="linear", ylim=None):
    ax.set_xlabel("Worker threads", labelpad=8)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.set_xticks(range(len(THREADS)))
    ax.set_xticklabels([str(t) for t in THREADS])
    ax.set_xlim(-0.18, len(THREADS) - 0.22)
    if yscale != "linear":
        ax.set_yscale(yscale)
    if ylim:
        ax.set_ylim(*ylim)
    ax.yaxis.set_major_formatter(FuncFormatter(yfmt or (lambda v, _: compact_number(v))))
    ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")
    ax.tick_params(axis="both", length=0, pad=6)


def direct_label(ax, df, metric, order, colors, offsets=None):
    offsets = offsets or {}
    for label in order:
        sub = df[df["label"] == label].sort_values("xpos")
        if sub.empty:
            continue
        last = sub.iloc[-1]
        ax.annotate(
            label,
            xy=(last["xpos"], last[metric]),
            xytext=(14, offsets.get(label, 0)),
            textcoords="offset points",
            va="center",
            ha="left",
            color=colors[label],
            fontsize=10.5,
            fontweight="bold" if label == "TideWAL" else "normal",
            clip_on=False,
        )


def plot_scaling(rows, workload_mode, metric, ylabel, output, caption,
                 yscale="linear", ylim=None, label_offsets=None):
    setup_style()
    df = pd.DataFrame([r for r in rows if r["workload_mode"] == workload_mode]).copy()
    df["xpos"] = df["thread_num"].map({t: i for i, t in enumerate(THREADS)})
    df["label"] = df["system"]
    fig, ax = plt.subplots(figsize=(7.15, 4.05), constrained_layout=True)
    for system in SYSTEM_ORDER:
        sub = df[df["system"] == system].sort_values("xpos")
        if sub.empty:
            continue
        ax.plot(
            sub["xpos"],
            sub[metric],
            color=COLORS[system],
            marker=MARKERS[system],
            linewidth=3.0 if system == "TideWAL" else 2.45,
            markersize=7.5,
            markerfacecolor="white",
            markeredgewidth=2.0,
            solid_capstyle="round",
            zorder=5 if system == "TideWAL" else 4,
        )
    decorate_axis(ax, ylabel, yscale=yscale, ylim=ylim)
    direct_label(ax, df, metric, SYSTEM_ORDER, COLORS, label_offsets)
    ax.text(
        0.0,
        1.04,
        caption,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10.5,
        color="#374151",
    )
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_workload_bars(rows, metric, ylabel, output, caption, yscale="linear"):
    setup_style()
    df = pd.DataFrame([r for r in rows if int(r["thread_num"]) == 32]).copy()
    df["workload"] = pd.Categorical(df["workload"], ["YCSB-A", "YCSB-B", "YCSB-C"])
    df["system"] = pd.Categorical(df["system"], SYSTEM_ORDER)
    fig, ax = plt.subplots(figsize=(7.15, 4.05), constrained_layout=True)
    sns.barplot(
        data=df,
        x="workload",
        y=metric,
        hue="system",
        hue_order=SYSTEM_ORDER,
        palette=COLORS,
        ax=ax,
        width=0.72,
    )
    if yscale != "linear":
        ax.set_yscale(yscale)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: compact_number(v)))
    ax.set_xlabel("")
    ax.set_ylabel(ylabel, labelpad=8)
    ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")
    ax.legend(title="", frameon=False, ncols=3, loc="upper left", bbox_to_anchor=(0, 1.13))
    ax.text(0.0, 1.04, caption, transform=ax.transAxes, ha="left", va="bottom", fontsize=10.5, color="#374151")
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_speedup(rows, output):
    setup_style()
    df = pd.DataFrame(rows)
    points = []
    for workload in ["ycsb_a", "ycsb_b", "ycsb_c"]:
        for thread in THREADS:
            pwal = df[(df["workload_mode"] == workload) & (df["system"] == "P-WAL") & (df["thread_num"] == thread)]
            tide = df[(df["workload_mode"] == workload) & (df["system"] == "TideWAL") & (df["thread_num"] == thread)]
            if pwal.empty or tide.empty:
                continue
            points.append({
                "workload": WORKLOAD_LABELS[workload],
                "thread_num": thread,
                "xpos": THREADS.index(thread),
                "speedup": float(tide["ack_tps"].iloc[0]) / max(float(pwal["ack_tps"].iloc[0]), 1.0),
            })
    pdf = pd.DataFrame(points)
    fig, ax = plt.subplots(figsize=(7.15, 4.05), constrained_layout=True)
    for workload in ["YCSB-A", "YCSB-B", "YCSB-C"]:
        sub = pdf[pdf["workload"] == workload].sort_values("xpos")
        if sub.empty:
            continue
        ax.plot(
            sub["xpos"],
            sub["speedup"],
            color=WORKLOAD_COLORS[workload],
            marker=MARKERS[workload],
            linewidth=2.7,
            markersize=7.5,
            markerfacecolor="white",
            markeredgewidth=2.0,
            solid_capstyle="round",
        )
    ax.axhline(1.0, color="#9ca3af", linewidth=1.0, linestyle="--")
    ax.set_xlabel("Worker threads", labelpad=8)
    ax.set_ylabel("TideWAL / P-WAL ack throughput", labelpad=8)
    ax.set_xticks(range(len(THREADS)))
    ax.set_xticklabels([str(t) for t in THREADS])
    ax.set_xlim(-0.18, len(THREADS) - 0.22)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}x"))
    ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")
    ax.legend(["YCSB-A", "YCSB-B", "YCSB-C"], frameon=False, loc="upper left")
    ax.text(
        0.0,
        1.04,
        "Where TideWAL helps most relative to per-transaction P-WAL",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10.5,
        color="#374151",
    )
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_tidewal_overhead(rows, output):
    setup_style()
    source = [
        r for r in rows
        if r["system"] == "TideWAL" and int(r["thread_num"]) == 32
    ]
    plot_rows = []
    components = [
        ("frontier_collect_ns_per_tx", "frontier collect"),
        ("frontier_publish_ns_per_tx", "frontier publish"),
        ("fdatasync_ns_per_tx", "fdatasync"),
    ]
    for row in source:
        for key, label in components:
            plot_rows.append({
                "workload": row["workload"],
                "component": label,
                "ns_per_tx": float(row.get(key, 0.0) or 0.0),
            })
    df = pd.DataFrame(plot_rows)
    fig, ax = plt.subplots(figsize=(7.15, 4.05), constrained_layout=True)
    sns.barplot(
        data=df,
        x="workload",
        y="ns_per_tx",
        hue="component",
        palette={
            "frontier collect": "#0f766e",
            "frontier publish": "#f97316",
            "fdatasync": "#6366f1",
        },
        ax=ax,
        width=0.72,
    )
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: compact_number(v)))
    ax.set_xlabel("")
    ax.set_ylabel("Cost [ns/tx]", labelpad=8)
    ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")
    ax.legend(title="", frameon=False, ncols=3, loc="upper left", bbox_to_anchor=(0, 1.13))
    ax.text(
        0.0,
        1.04,
        "TideWAL cost breakdown at 32 workers",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10.5,
        color="#374151",
    )
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workloads", default="ycsb_a,ycsb_c")
    parser.add_argument("--threads", default="1,2,4,8,16,32")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--logger-num", type=int, default=4)
    parser.add_argument("--committer-num", type=int, default=1)
    parser.add_argument("--group-size", type=int, default=16)
    parser.add_argument("--flush-us", type=int, default=50)
    parser.add_argument("--max-pending", type=int, default=65536)
    parser.add_argument("--input-csv", default="")
    parser.add_argument("--skip-run", action="store_true")
    args = parser.parse_args()

    workloads = parse_workloads(args.workloads)
    threads = parse_int_list(args.threads)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS / f"ycsbabc_tidewal_figures_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = []

    if args.input_csv:
        raw_rows = read_rows(ROOT / args.input_csv if not Path(args.input_csv).is_absolute() else args.input_csv)
    elif not args.skip_run:
        for repeat in range(args.repeats):
            for workload in workloads:
                for thread in threads:
                    for mode in MODES:
                        row = run_case(out_dir, workload, mode, repeat, thread, args)
                        raw_rows.append(row)
                        print(
                            f"ycsbabc repeat={repeat} workload={workload} thread={thread} "
                            f"mode={mode} ack_tps={row['durable_ack_tps']} "
                            f"p99={row['ack_latency_p99_us']} pending={row['pending_commits']}",
                            flush=True,
                        )

    raw_csv = out_dir / f"ycsbabc_tidewal_raw_{stamp}.csv"
    if raw_rows:
        write_csv(raw_csv, raw_rows)

    plot_rows = aggregate_raw_rows(raw_rows) + load_ycsbb_summary()
    write_plot_csv(plot_rows)

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    outputs = []
    for workload in workloads:
        suffix = workload.replace("_", "")
        plot_scaling(
            plot_rows,
            workload,
            "ack_tps",
            "Ack throughput [tx/s]",
            FIG_DIR / f"fig_{suffix}_tidewal_ack_tps.pdf",
            WORKLOAD_CAPTIONS[workload],
            ylim=None,
            label_offsets={"Single WAL": 8, "P-WAL": 2, "TideWAL": 0},
        )
        outputs.append(FIG_DIR / f"fig_{suffix}_tidewal_ack_tps.pdf")
        plot_scaling(
            plot_rows,
            workload,
            "p99_us",
            "p99 latency [us]",
            FIG_DIR / f"fig_{suffix}_tidewal_latency.pdf",
            f"{WORKLOAD_LABELS[workload]}, p99 durable-ack latency",
            yscale="log",
            label_offsets={"Single WAL": 0, "P-WAL": -12, "TideWAL": 12},
        )
        outputs.append(FIG_DIR / f"fig_{suffix}_tidewal_latency.pdf")
        plot_scaling(
            plot_rows,
            workload,
            "pending",
            "Pending durable commits",
            FIG_DIR / f"fig_{suffix}_tidewal_pending.pdf",
            f"{WORKLOAD_LABELS[workload]}, pending commits at the measurement boundary",
            label_offsets={"Single WAL": 2, "P-WAL": 17, "TideWAL": 0},
        )
        outputs.append(FIG_DIR / f"fig_{suffix}_tidewal_pending.pdf")

    plot_workload_bars(
        plot_rows,
        "ack_tps",
        "Ack throughput [tx/s]",
        FIG_DIR / "fig_ycsbabc_32thread_throughput.pdf",
        "32-worker workload sensitivity",
    )
    outputs.append(FIG_DIR / "fig_ycsbabc_32thread_throughput.pdf")
    plot_workload_bars(
        plot_rows,
        "p99_us",
        "p99 latency [us]",
        FIG_DIR / "fig_ycsbabc_32thread_latency.pdf",
        "32-worker tail latency across workload mixes",
        yscale="log",
    )
    outputs.append(FIG_DIR / "fig_ycsbabc_32thread_latency.pdf")
    plot_speedup(plot_rows, FIG_DIR / "fig_ycsbabc_tidewal_speedup_vs_pwal.pdf")
    outputs.append(FIG_DIR / "fig_ycsbabc_tidewal_speedup_vs_pwal.pdf")
    plot_tidewal_overhead(plot_rows, FIG_DIR / "fig_ycsbabc_tidewal_overhead_32thread.pdf")
    outputs.append(FIG_DIR / "fig_ycsbabc_tidewal_overhead_32thread.pdf")

    print(raw_csv)
    print(OUT_CSV)
    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
