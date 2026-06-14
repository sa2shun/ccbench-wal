#!/usr/bin/env python3
import argparse
import csv
import os
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
    write_csv,
)
from run_ycsbabc_ayame_worker_threads import (
    COLORS,
    MARKERS,
    SYSTEM_LABELS,
    SYSTEM_ORDER,
    compact_number,
    display_system,
    mode_allocation,
    parse_metrics,
    safe_float,
)


FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"
OUT_CSV = TABLE_DIR / "ycsbb_perf_scaling_20260609.csv"
OUT_DOC = ROOT / "docs" / "ycsbb_perf_scaling_20260609.md"

MODES = ["single_wal", "pwal", "tidewal"]
EVENTS = ["task-clock", "context-switches"]


def parse_int_list(text):
    return [int(x) for x in text.split(",") if x]


def parse_perf_stat(path):
    result = {}
    with Path(path).open(newline="") as f:
        for row in csv.reader(f):
            if len(row) < 3:
                continue
            value = row[0].strip()
            event = row[2].strip()
            if not event or "<not" in value:
                continue
            try:
                result[event] = float(value)
            except ValueError:
                pass
    return result


def workspace_path(path):
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def ycsb_cmd(worker, seconds):
    preset = WORKLOAD_PRESETS["ycsb_b"]
    return [
        str(PWAL_YCSB_EXE),
        f"--thread_num={worker}",
        f"--extime={seconds}",
        "--clocks_per_us=1800",  # host Xeon Gold 5418N invariant TSC; binary default 2100 is wrong here
        "--ycsb_tuple_num=100000",
        f"--ycsb_max_ope={preset['ycsb_max_ope']}",
        f"--ycsb_rratio={preset['ycsb_rratio']}",
    ]


def run_case(out_dir, mode, worker, repeat, args):
    alloc = mode_allocation(mode, worker)
    logger = alloc["logger_num"]
    committer = alloc["committer_threads"]
    case = f"ycsb_b_{mode}_w{worker}_r{repeat}"
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir = out_dir / "wal" / case

    env = os.environ.copy()
    env["CCBENCH_WAL_DIR"] = str(wal_dir)
    env["CCBENCH_WAL_SKIP_READ_ONLY"] = "1"
    env["CCBENCH_WAL_DURABLE_MODE"] = "sync"
    env["CCBENCH_WAL_GROUP_SIZE"] = str(args.group_size)
    env["CCBENCH_WAL_FLUSH_US"] = str(args.flush_us)
    env["CCBENCH_WAL_MAX_PENDING"] = str(args.max_pending)

    if mode == "single_wal":
        env["CCBENCH_WAL_MODE"] = "shared"
        env["CCBENCH_WAL_LOGGER_NUM"] = str(worker)
        env["CCBENCH_WAL_COMMITTER_NUM"] = "1"
    elif mode == "pwal":
        env["CCBENCH_WAL_MODE"] = "per_thread"
        env["CCBENCH_WAL_LOGGER_NUM"] = str(logger)
        env["CCBENCH_WAL_COMMITTER_NUM"] = "1"
    elif mode == "tidewal":
        env["CCBENCH_WAL_MODE"] = "per_thread"
        env["CCBENCH_WAL_DURABLE_MODE"] = "async_dep_frontier_cstamp"
        env["CCBENCH_WAL_LOGGER_NUM"] = str(logger)
        env["CCBENCH_WAL_COMMITTER_NUM"] = str(committer)
    else:
        raise ValueError(f"unknown mode: {mode}")

    out_path = logs / f"{case}.out"
    err_path = logs / f"{case}.err"
    perf_path = logs / f"{case}.perfstat.csv"
    cmd = [
        "perf",
        "stat",
        "-x,",
        "-e",
        ",".join(EVENTS),
        "-o",
        str(perf_path),
        "--",
        *ycsb_cmd(worker, args.seconds),
    ]
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)

    row = parse_metrics(out_path.read_text(errors="replace"))
    row.update(
        {
            "workload_mode": "ycsb_b",
            "workload": "YCSB-B",
            "mode": mode,
            "system": SYSTEM_LABELS[mode],
            "repeat": str(repeat),
            "worker_threads": str(worker),
            "wal_streams": str(alloc["wal_streams"]),
            "flusher_threads": str(alloc["flusher_threads"]),
            "committer_threads": str(alloc["committer_threads"]),
            "logger_num": str(logger),
            "total_active_threads": str(
                worker + logger + committer if mode == "tidewal" else worker
            ),
            "seconds": str(args.seconds),
            "group_size": str(args.group_size if mode == "tidewal" else 0),
            "flush_us": str(args.flush_us if mode == "tidewal" else 0),
            "max_pending": str(args.max_pending if mode == "tidewal" else 0),
            "exit_code": str(proc.returncode),
            "stdout_log": str(out_path.relative_to(ROOT)),
            "stderr_log": str(err_path.relative_to(ROOT)),
            "perfstat_log": str(perf_path.relative_to(ROOT)),
        }
    )
    row = derived(row)
    perf = parse_perf_stat(perf_path)
    actual = (
        safe_float(row.get("actual_extime"))
        or safe_float(row.get("actual_sec"))
        or args.seconds
    )
    acked = safe_float(row.get("derived_acked_commits")) or safe_float(
        row.get("derived_logical_commits")
    )
    task_clock_ms = perf.get("task-clock", 0.0)
    context_switches = perf.get("context-switches", 0.0)
    row.update(
        {
            "ack_tps": f"{acked / max(actual, 1.0):.6f}",
            "perf_task_clock_ms": f"{task_clock_ms:.6f}",
            "cpu_cores": f"{task_clock_ms / max(actual * 1000.0, 1.0):.6f}",
            "context_switches": f"{context_switches:.0f}",
            "context_switches_per_sec": f"{context_switches / max(actual, 1.0):.6f}",
        }
    )
    return row


def aggregate(rows):
    grouped = {}
    for row in rows:
        key = (display_system(row), int(row["worker_threads"]))
        grouped.setdefault(key, []).append(row)
    out = []
    for (system, worker), rs in sorted(grouped.items()):
        def avg(key):
            return sum(safe_float(r.get(key)) for r in rs) / len(rs)

        out.append(
            {
                "workload": "YCSB-B",
                "system": system,
                "worker_threads": worker,
                "wal_streams": avg("wal_streams"),
                "flusher_threads": avg("flusher_threads"),
                "committer_threads": avg("committer_threads"),
                "total_active_threads": avg("total_active_threads"),
                "ack_tps": avg("ack_tps"),
                "cpu_cores": avg("cpu_cores"),
                "context_switches": avg("context_switches"),
                "context_switches_per_sec": avg("context_switches_per_sec"),
            }
        )
    return out


def setup_style():
    sns.set_theme(
        context="paper",
        style="whitegrid",
        font_scale=1.5,
        rc={
            "font.family": "DejaVu Sans",
            "axes.labelsize": 15,
            "axes.titlesize": 16,
            "xtick.labelsize": 13,
            "ytick.labelsize": 13,
            "legend.fontsize": 14,
            "axes.edgecolor": "#d1d5db",
            "axes.linewidth": 0.9,
            "grid.color": "#e5e7eb",
            "grid.linewidth": 0.85,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        },
    )
    plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white"})


def draw_metric(rows, metric, ylabel, output, title):
    setup_style()
    df = pd.DataFrame(rows)
    df["system"] = pd.Categorical(df["system"], SYSTEM_ORDER, ordered=True)
    workers = sorted(df["worker_threads"].unique())
    fig, ax = plt.subplots(figsize=(4.7, 3.6), constrained_layout=True)
    for system in SYSTEM_ORDER:
        sub = df[df["system"] == system].sort_values("worker_threads")
        if sub.empty:
            continue
        ax.plot(
            sub["worker_threads"],
            sub[metric],
            color=COLORS[system],
            marker=MARKERS[system],
            linewidth=2.6 if system == "Ayame" else 2.25,
            markersize=6.5,
            markerfacecolor="white",
            markeredgewidth=1.8,
            solid_capstyle="round",
            label=system,
        )
    ax.set_title(title, loc="left", fontsize=16, fontweight="bold", pad=10)
    ax.set_xlabel("Worker threads")
    ax.set_ylabel(ylabel)
    ax.set_xticks(workers)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: compact_number(v)))
    ax.grid(True, axis="y")
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False, loc="best")
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def draw_combined(rows, output):
    setup_style()
    df = pd.DataFrame(rows)
    df["system"] = pd.Categorical(df["system"], SYSTEM_ORDER, ordered=True)
    workers = sorted(df["worker_threads"].unique())
    panels = [
        ("ack_tps", "Ack throughput [tx/s]", "Throughput"),
        ("cpu_cores", "CPU cores used", "CPU cores"),
        ("context_switches", "Context switches / run", "Context switches"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(12.0, 3.8), constrained_layout=True)
    for ax, (metric, ylabel, title) in zip(axes, panels):
        for system in SYSTEM_ORDER:
            sub = df[df["system"] == system].sort_values("worker_threads")
            if sub.empty:
                continue
            ax.plot(
                sub["worker_threads"],
                sub[metric],
                color=COLORS[system],
                marker=MARKERS[system],
                linewidth=2.6 if system == "Ayame" else 2.25,
                markersize=6.0,
                markerfacecolor="white",
                markeredgewidth=1.7,
                solid_capstyle="round",
                label=system,
            )
        ax.set_title(title, fontsize=16, fontweight="bold")
        ax.set_xlabel("Worker threads")
        ax.set_ylabel(ylabel)
        ax.set_xticks(workers)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: compact_number(v)))
        ax.grid(True, axis="y")
        ax.grid(False, axis="x")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncols=3, frameon=False, loc="upper right", bbox_to_anchor=(0.99, 1.08))
    fig.text(0.01, 1.03, "YCSB-B perf stat worker-thread scaling", ha="left", va="bottom",
             fontsize=13, color="#374151")
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def write_report(summary, raw_csv, outputs, args):
    with OUT_DOC.open("w") as f:
        print("# YCSB-B perf stat worker scaling", file=f)
        print("", file=f)
        print(f"date: {datetime.now().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("`perf stat` was used to collect `task-clock` and `context-switches`.", file=f)
        print("CPU cores are computed as `task-clock-ms / elapsed-ms`.", file=f)
        print("", file=f)
        print("## Conditions", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---|", file=f)
        print(f"| workload | YCSB-B |", file=f)
        print(f"| worker threads | {args.workers} |", file=f)
        print(f"| systems | Single WAL, P-WAL, Ayame |", file=f)
        print(f"| seconds | {args.seconds} |", file=f)
        print(f"| repeats | {args.repeats} |", file=f)
        print(f"| Ayame group_size | {args.group_size} |", file=f)
        print(f"| Ayame flush_us | {args.flush_us} |", file=f)
        print(f"| Ayame max_pending | {args.max_pending} |", file=f)
        print("", file=f)
        print(f"summary csv: `{OUT_CSV.relative_to(ROOT)}`", file=f)
        print(f"raw csv: `{raw_csv.relative_to(ROOT)}`", file=f)
        print("", file=f)
        print("## Figures", file=f)
        print("", file=f)
        for output in outputs:
            print(f"- `{output.relative_to(ROOT)}`", file=f)
        print("", file=f)
        print("## Summary", file=f)
        print("", file=f)
        print("| system | workers | ack tps | CPU cores | context switches |", file=f)
        print("|---|---:|---:|---:|---:|", file=f)
        for system in SYSTEM_ORDER:
            for row in [r for r in summary if r["system"] == system]:
                print(
                    f"| {system} | {row['worker_threads']} | {row['ack_tps']:.0f} | "
                    f"{row['cpu_cores']:.2f} | {row['context_switches']:.0f} |",
                    file=f,
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", default="1,2,4,8,16,32")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--group-size", type=int, default=16)
    parser.add_argument("--flush-us", type=int, default=50)
    parser.add_argument("--max-pending", type=int, default=65536)
    parser.add_argument("--input-csv")
    args = parser.parse_args()

    workers = parse_int_list(args.workers)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS / f"ycsbb_perf_scaling_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.input_csv:
        raw_csv = workspace_path(args.input_csv)
        with raw_csv.open(newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        rows = []
        for repeat in range(args.repeats):
            for worker in workers:
                for mode in MODES:
                    row = run_case(out_dir, mode, worker, repeat, args)
                    rows.append(row)
                    print(
                        f"repeat={repeat} worker={worker} system={row['system']} "
                        f"ack_tps={float(row['ack_tps']):.0f} cpu={float(row['cpu_cores']):.2f} "
                        f"ctx={float(row['context_switches']):.0f}",
                        flush=True,
                    )
        raw_csv = out_dir / f"ycsbb_perf_scaling_raw_{stamp}.csv"
        write_csv(raw_csv, rows)

    summary = aggregate(rows)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_CSV, summary)

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    outputs = [
        FIG_DIR / "fig_ycsbb_perf_scaling_perf_stat.pdf",
        FIG_DIR / "fig_ycsbb_perf_scaling_tps.pdf",
        FIG_DIR / "fig_ycsbb_perf_scaling_cpu_cores.pdf",
        FIG_DIR / "fig_ycsbb_perf_scaling_context_switches.pdf",
    ]
    draw_combined(summary, outputs[0])
    draw_metric(summary, "ack_tps", "Durable ack throughput [tx/s]", outputs[1], "YCSB-B throughput")
    draw_metric(summary, "cpu_cores", "CPU cores used", outputs[2], "YCSB-B CPU utilization")
    draw_metric(summary, "context_switches", "Context switches / run", outputs[3], "YCSB-B context switches")
    write_report(summary, raw_csv, outputs, args)

    print(raw_csv)
    print(OUT_CSV)
    for output in outputs:
        print(output)
    print(OUT_DOC)


if __name__ == "__main__":
    main()
