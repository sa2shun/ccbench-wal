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
from matplotlib.ticker import FuncFormatter

from run_ermia_cstamp_pwal_experiments import (
    PWAL_YCSB_EXE,
    RESULTS,
    ROOT,
    WORKLOAD_PRESETS,
    derived,
    fnum,
    mean,
    stdev,
    write_csv,
)


FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"
WAL_YCSB_EXE = ROOT / "build" / "cc" / "ermia_wal" / "ycsb_ermia_wal.exe"

MODES = [
    "ermia_single_wal",
    "ermia_plain_pwal",
    "ermia_async_dep_frontier_cstamp",
]

LABELS = {
    "ermia_single_wal": "Single WAL",
    "ermia_plain_pwal": "P-WAL",
    "ermia_async_dep_frontier_cstamp": "Async dep frontier cstamp",
}

COLORS = {
    "ermia_single_wal": "#9f1239",
    "ermia_plain_pwal": "#ea580c",
    "ermia_async_dep_frontier_cstamp": "#059669",
}

MARKERS = {
    "ermia_single_wal": "o",
    "ermia_plain_pwal": "s",
    "ermia_async_dep_frontier_cstamp": "^",
}


def parse_int_list(text):
    return [int(x) for x in text.split(",") if x]


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        match = re.match(r"^#?([A-Za-z0-9_]+):\s*(.*)$", line)
        if match:
            row[match.group(1)] = match.group(2).strip()
    return row


def fmt_y(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1000:.0f}K"
    return f"{v:.0f}"


def run_case(out_dir, mode, repeat, thread_num, args):
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir = out_dir / "wal"
    preset = WORKLOAD_PRESETS["ycsb_b"]

    env = os.environ.copy()
    env["CCBENCH_WAL_DIR"] = str(wal_dir)
    if mode == "ermia_single_wal":
        exe = WAL_YCSB_EXE
    else:
        exe = PWAL_YCSB_EXE
    if mode == "ermia_plain_pwal":
        env["CCBENCH_WAL_DURABLE_MODE"] = "sync"
        env["CCBENCH_WAL_LOGGER_NUM"] = str(thread_num)
    elif mode == "ermia_async_dep_frontier_cstamp":
        env["CCBENCH_WAL_DURABLE_MODE"] = "async_dep_frontier_cstamp"
        env["CCBENCH_WAL_LOGGER_NUM"] = str(args.logger_num)
        env["CCBENCH_WAL_COMMITTER_NUM"] = str(args.committer_num)
        env["CCBENCH_WAL_GROUP_SIZE"] = str(args.group_size)
        env["CCBENCH_WAL_FLUSH_US"] = str(args.flush_us)
        env["CCBENCH_WAL_MAX_PENDING"] = str(args.max_pending)

    cmd = [
        str(exe),
        f"--thread_num={thread_num}",
        f"--extime={args.seconds}",
        "--ycsb_tuple_num=100000",
        f"--ycsb_max_ope={preset['ycsb_max_ope']}",
        f"--ycsb_rratio={preset['ycsb_rratio']}",
    ]
    label = f"ycsb_b_{mode}_th{thread_num}_r{repeat}"
    out_path = logs / f"{label}.out"
    err_path = logs / f"{label}.err"
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)

    row = parse_metrics(out_path.read_text(errors="replace"))
    row.update(
        {
            "mode": mode,
            "workload_mode": "ycsb_b",
            "repeat": str(repeat),
            "thread_num": str(thread_num),
            "worker_threads": str(thread_num),
            "seconds": str(args.seconds),
            "logger_num": str(args.logger_num if mode == "ermia_async_dep_frontier_cstamp" else thread_num),
            "committer_num": str(args.committer_num if mode == "ermia_async_dep_frontier_cstamp" else 0),
            "group_size": str(args.group_size if mode == "ermia_async_dep_frontier_cstamp" else 0),
            "flush_us": str(args.flush_us if mode == "ermia_async_dep_frontier_cstamp" else 0),
            "max_pending": str(args.max_pending if mode == "ermia_async_dep_frontier_cstamp" else 0),
            "exit_code": str(proc.returncode),
            "stdout_log": str(out_path),
            "stderr_log": str(err_path),
        }
    )
    row = derived(row)
    if fnum(row, "durable_ack_tps") == 0:
        row["durable_ack_tps"] = row.get("throughput[tps]", "0")
    return row


def plot_throughput(path, rows, threads):
    grouped = {}
    for row in rows:
        grouped.setdefault((row["mode"], int(row["thread_num"])), []).append(row)

    plt.rcParams.update({
        "font.size": 10,
        "axes.labelsize": 11,
        "axes.titlesize": 14,
        "legend.fontsize": 9,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
    fig, ax = plt.subplots(figsize=(7.0, 4.35), constrained_layout=True)
    x = list(range(len(threads)))
    for mode in MODES:
        ys = [mean(grouped.get((mode, th), []), "durable_ack_tps") for th in threads]
        errs = [stdev(grouped.get((mode, th), []), "durable_ack_tps") for th in threads]
        ax.plot(
            x,
            ys,
            label=LABELS[mode],
            color=COLORS[mode],
            marker=MARKERS[mode],
            linewidth=2.8,
            markersize=6.5,
        )
        lower = [max(0.0, y - e) for y, e in zip(ys, errs)]
        upper = [y + e for y, e in zip(ys, errs)]
        ax.fill_between(x, lower, upper, color=COLORS[mode], alpha=0.12, linewidth=0)
        ax.annotate(
            fmt_y(ys[-1]),
            xy=(x[-1], ys[-1]),
            xytext=(8, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color=COLORS[mode],
            fontweight="bold",
        )

    ax.set_title("YCSB-B throughput: Single WAL vs P-WAL vs Cstamp-PWAL")
    ax.set_xlabel("Worker threads")
    ax.set_ylabel("Ack throughput [tx/s]")
    ax.set_xticks(x)
    ax.set_xticklabels([str(th) for th in threads])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_y(v)))
    ax.grid(True, axis="y", color="#d4d4d8", linewidth=0.8)
    ax.grid(True, axis="x", color="#eeeeee", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", frameon=False)
    ax.text(
        0.0,
        1.01,
        "YCSB-B, 95% read / 5% update, 10 ops/tx; async mode uses logger=8, committer=1",
        transform=ax.transAxes,
        fontsize=8,
        color="#52525b",
    )
    fig.savefig(path)
    plt.close(fig)


def write_summary(path, rows, threads, pdf_path, args):
    grouped = {}
    for row in rows:
        grouped.setdefault((row["mode"], int(row["thread_num"])), []).append(row)
    with path.open("w") as f:
        print("# YCSB-B WAL/P-WAL/Cstamp-PWAL scaling", file=f)
        print("", file=f)
        print(f"date: {datetime.now().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("## Conditions", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---|", file=f)
        print("| workload | YCSB-B: 95% read / 5% update, 10 ops/tx |", file=f)
        print(f"| worker threads | {','.join(map(str, threads))} |", file=f)
        print(f"| repeats | {args.repeats} |", file=f)
        print(f"| seconds | {args.seconds} |", file=f)
        print(f"| async logger_num | {args.logger_num} |", file=f)
        print(f"| async committer_num | {args.committer_num} |", file=f)
        print(f"| async group_size | {args.group_size} |", file=f)
        print(f"| async flush_us | {args.flush_us} |", file=f)
        print(f"| async max_pending | {args.max_pending} |", file=f)
        print("", file=f)
        print(f"PDF: `{pdf_path.relative_to(ROOT)}`", file=f)
        print("", file=f)
        print("## Summary", file=f)
        print("", file=f)
        print("| thread | mode | ack tps mean | ack tps stdev | pending |", file=f)
        print("|---:|---|---:|---:|---:|", file=f)
        for th in threads:
            for mode in MODES:
                rs = grouped.get((mode, th), [])
                print(
                    f"| {th} | {LABELS[mode]} | "
                    f"{mean(rs, 'durable_ack_tps'):.0f} | "
                    f"{stdev(rs, 'durable_ack_tps'):.1f} | "
                    f"{mean(rs, 'pending_commits'):.0f} |",
                    file=f,
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", default="1,2,4,8,16,32")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--logger-num", type=int, default=8)
    parser.add_argument("--committer-num", type=int, default=1)
    parser.add_argument("--group-size", type=int, default=8)
    parser.add_argument("--flush-us", type=int, default=100)
    parser.add_argument("--max-pending", type=int, default=65536)
    parser.add_argument("--skip-run", action="store_true")
    args = parser.parse_args()

    threads = parse_int_list(args.threads)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS / f"ermia_wal_pwal_cstamp_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    if not args.skip_run:
        for repeat in range(args.repeats):
            for th in threads:
                for mode in MODES:
                    row = run_case(out_dir, mode, repeat, th, args)
                    rows.append(row)
                    print(
                        f"wal_pwal_cstamp repeat={repeat} thread={th} mode={mode} "
                        f"ack_tps={row['durable_ack_tps']} pending={row['pending_commits']}",
                        flush=True,
                    )

    csv_path = out_dir / f"ermia_wal_pwal_cstamp_{stamp}.csv"
    write_csv(csv_path, rows)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    tracked_csv = TABLE_DIR / "ycsbb_wal_pwal_cstamp_scaling.csv"
    write_csv(tracked_csv, rows)

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = FIG_DIR / "fig_ycsbb_wal_pwal_cstamp_ack_tps.pdf"
    plot_throughput(pdf_path, rows, threads)
    summary = ROOT / "docs" / "ycsbb_wal_pwal_cstamp_scaling_20260607.md"
    write_summary(summary, rows, threads, pdf_path, args)

    print(out_dir)
    print(summary)
    print(pdf_path)


if __name__ == "__main__":
    main()
