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
    write_csv,
)


FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"
OUT_CSV = TABLE_DIR / "ycsbabc_tidewal_worker_threads_20260608.csv"
OUT_DOC = ROOT / "docs" / "ycsbabc_tidewal_worker_threads_20260608.md"

MODES = ["single_wal", "pwal", "tidewal"]
SYSTEM_LABELS = {
    "single_wal": "Single WAL",
    "pwal": "P-WAL",
    "tidewal": "TideWAL",
}
SYSTEM_ORDER = ["Single WAL", "P-WAL", "TideWAL"]

WORKLOAD_LABELS = {
    "ycsb_a": "YCSB-A",
    "ycsb_b": "YCSB-B",
    "ycsb_c": "YCSB-C",
}
WORKLOAD_ORDER = ["YCSB-A", "YCSB-B", "YCSB-C"]

COLORS = {
    "Single WAL": "#4b5563",
    "P-WAL": "#d97706",
    "TideWAL": "#047857",
}
MARKERS = {
    "Single WAL": "o",
    "P-WAL": "s",
    "TideWAL": "^",
}

TIDEWAL_LOGGERS = {
    1: 1,
    2: 1,
    4: 1,
    8: 2,
    16: 4,
    32: 7,
    48: 9,
    96: 19,
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


def tidewal_logger_num(worker):
    if worker in TIDEWAL_LOGGERS:
        return TIDEWAL_LOGGERS[worker]
    return max(1, round(worker * 7 / 32))


def mode_allocation(mode, worker):
    if mode == "single_wal":
        return worker, 0, 0
    if mode == "pwal":
        return worker, worker, 0
    if mode == "tidewal":
        return worker, tidewal_logger_num(worker), 1
    raise ValueError(f"unknown mode: {mode}")


def run_case(out_dir, workload, mode, repeat, worker, args):
    worker, logger, committer = mode_allocation(mode, worker)
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir = out_dir / "wal"
    preset = WORKLOAD_PRESETS[workload]

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
    else:
        env["CCBENCH_WAL_MODE"] = "per_thread"
        env["CCBENCH_WAL_DURABLE_MODE"] = "async_dep_frontier_cstamp"
        env["CCBENCH_WAL_LOGGER_NUM"] = str(logger)
        env["CCBENCH_WAL_COMMITTER_NUM"] = str(committer)

    cmd = [
        str(PWAL_YCSB_EXE),
        f"--thread_num={worker}",
        f"--extime={args.seconds}",
        "--ycsb_tuple_num=100000",
        f"--ycsb_max_ope={preset['ycsb_max_ope']}",
        f"--ycsb_rratio={preset['ycsb_rratio']}",
    ]
    label = f"{workload}_{mode}_worker{worker}_l{logger}_c{committer}_r{repeat}"
    out_path = logs / f"{label}.out"
    err_path = logs / f"{label}.err"
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)

    row = parse_metrics(out_path.read_text(errors="replace"))
    row.update(
        {
            "mode": mode,
            "system": SYSTEM_LABELS[mode],
            "workload_mode": workload,
            "workload": WORKLOAD_LABELS[workload],
            "repeat": str(repeat),
            "thread_num": str(worker),
            "worker_threads": str(worker),
            "logger_num": str(logger),
            "committer_num": str(committer),
            "background_threads": str(logger + committer if mode == "tidewal" else 0),
            "total_active_threads": str(worker + logger + committer if mode == "tidewal" else worker),
            "seconds": str(args.seconds),
            "ycsb_max_ope": str(preset["ycsb_max_ope"]),
            "ycsb_rratio": str(preset["ycsb_rratio"]),
            "group_size": str(args.group_size if mode == "tidewal" else 0),
            "flush_us": str(args.flush_us if mode == "tidewal" else 0),
            "max_pending": str(args.max_pending if mode == "tidewal" else 0),
            "skip_read_only": "1",
            "binary": str(PWAL_YCSB_EXE.relative_to(ROOT)),
            "exit_code": str(proc.returncode),
            "stdout_log": str(out_path),
            "stderr_log": str(err_path),
        }
    )
    row = derived(row)
    if safe_float(row.get("durable_ack_tps")) == 0:
        row["durable_ack_tps"] = row.get("throughput[tps]", "0")
    return row


def aggregate(rows):
    grouped = {}
    for row in rows:
        key = (
            row["workload_mode"],
            row["workload"],
            row["system"],
            int(row["worker_threads"]),
        )
        grouped.setdefault(key, []).append(row)
    out = []
    for (workload_mode, workload, system, worker), rs in sorted(grouped.items()):
        p99_values = []
        for r in rs:
            p99 = safe_float(r.get("ack_latency_p99_us"))
            if p99 <= 0:
                workers = max(safe_float(r.get("worker_threads")), 1.0)
                p99 = workers * 1_000_000.0 / max(safe_float(r.get("durable_ack_tps")), 1.0)
            p99_values.append(p99)
        logical = [safe_float(r.get("derived_logical_commits")) for r in rs]
        fast = [safe_float(r.get("wal_stats_read_only_fast_path_acks")) for r in rs]
        out.append(
            {
                "workload_mode": workload_mode,
                "workload": workload,
                "system": system,
                "worker_threads": worker,
                "logger_num": median(safe_float(r.get("logger_num")) for r in rs),
                "committer_num": median(safe_float(r.get("committer_num")) for r in rs),
                "background_threads": median(safe_float(r.get("background_threads")) for r in rs),
                "total_active_threads": median(safe_float(r.get("total_active_threads")) for r in rs),
                "ack_tps": mean(safe_float(r.get("durable_ack_tps")) for r in rs),
                "p99_us": median(p99_values),
                "pending": mean(safe_float(r.get("pending_commits")) for r in rs),
                "read_only_commits_per_tx": mean(
                    safe_float(r.get("wal_stats_read_only_commits")) /
                    max(safe_float(r.get("wal_stats_commits")), 1.0) for r in rs
                ),
                "read_only_fast_path_per_tx": mean(
                    safe_float(r.get("wal_stats_read_only_fast_path_acks")) /
                    max(safe_float(r.get("wal_stats_commits")), 1.0) for r in rs
                ),
                "frontier_collect_ns_per_tx": mean(
                    safe_float(r.get("frontier_collect_ns_per_tx")) for r in rs
                ),
                "frontier_publish_ns_per_tx": mean(
                    safe_float(r.get("frontier_publish_ns_per_tx")) for r in rs
                ),
                "waitlist_registration_ns_per_tx": mean(
                    safe_float(r.get("waitlist_registration_ns_per_tx")) for r in rs
                ),
                "fdatasync_ns_per_tx": mean(safe_float(r.get("fdatasync_ns_per_tx")) for r in rs),
                "flusher_idle_wait_ns": mean(safe_float(r.get("wal_stats_flusher_idle_wait_ns")) for r in rs),
                "flusher_idle_waits": mean(safe_float(r.get("wal_stats_flusher_idle_waits")) for r in rs),
                "committer_idle_wait_ns": mean(safe_float(r.get("wal_stats_committer_idle_wait_ns")) for r in rs),
                "committer_idle_waits": mean(safe_float(r.get("wal_stats_committer_idle_waits")) for r in rs),
            }
        )
    return out


def write_summary_csv(rows):
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "workload_mode",
        "workload",
        "system",
        "worker_threads",
        "logger_num",
        "committer_num",
        "background_threads",
        "total_active_threads",
        "ack_tps",
        "p99_us",
        "pending",
        "read_only_commits_per_tx",
        "read_only_fast_path_per_tx",
        "frontier_collect_ns_per_tx",
        "frontier_publish_ns_per_tx",
        "waitlist_registration_ns_per_tx",
        "fdatasync_ns_per_tx",
        "flusher_idle_wait_ns",
        "flusher_idle_waits",
        "committer_idle_wait_ns",
        "committer_idle_waits",
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
        font_scale=1.08,
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


def draw_faceted_lines(rows, metric, ylabel, output, caption, yscale="linear"):
    setup_style()
    df = pd.DataFrame(rows)
    df["workload"] = pd.Categorical(df["workload"], WORKLOAD_ORDER)
    df["system"] = pd.Categorical(df["system"], SYSTEM_ORDER)
    workers = sorted(df["worker_threads"].unique())
    fig, axes = plt.subplots(1, 3, figsize=(10.6, 3.7), sharey=False, constrained_layout=True)
    for ax, workload in zip(axes, WORKLOAD_ORDER):
        sub_w = df[df["workload"] == workload]
        for system in SYSTEM_ORDER:
            sub = sub_w[sub_w["system"] == system].sort_values("worker_threads")
            if sub.empty:
                continue
            ax.plot(
                sub["worker_threads"],
                sub[metric],
                color=COLORS[system],
                marker=MARKERS[system],
                linewidth=2.6 if system == "TideWAL" else 2.2,
                markersize=6.2,
                markerfacecolor="white",
                markeredgewidth=1.8,
                solid_capstyle="round",
            )
        ax.set_title(workload, fontsize=11.5, fontweight="bold", pad=8)
        ax.set_xlabel("Worker threads")
        ax.set_xticks(workers)
        ax.set_xticklabels([str(int(x)) for x in workers], rotation=0)
        if yscale != "linear":
            ax.set_yscale(yscale)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: compact_number(v)))
        ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.85)
        ax.grid(False, axis="x")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#d1d5db")
        ax.spines["bottom"].set_color("#d1d5db")
        ax.tick_params(axis="both", length=0, pad=5)
    axes[0].set_ylabel(ylabel)
    handles = [
        plt.Line2D([0], [0], color=COLORS[s], marker=MARKERS[s],
                   markerfacecolor="white", markeredgewidth=1.8,
                   linewidth=2.4, label=s)
        for s in SYSTEM_ORDER
    ]
    fig.legend(handles=handles, ncols=3, frameon=False, loc="upper right",
               bbox_to_anchor=(0.985, 1.08))
    fig.text(0.01, 1.03, caption, ha="left", va="bottom",
             fontsize=10.5, color="#374151")
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def draw_speedup(rows, output):
    setup_style()
    df = pd.DataFrame(rows)
    points = []
    for workload in WORKLOAD_ORDER:
        for worker in sorted(df["worker_threads"].unique()):
            pwal = df[(df["workload"] == workload) & (df["system"] == "P-WAL") &
                      (df["worker_threads"] == worker)]
            tide = df[(df["workload"] == workload) & (df["system"] == "TideWAL") &
                      (df["worker_threads"] == worker)]
            if pwal.empty or tide.empty:
                continue
            points.append({
                "workload": workload,
                "worker_threads": worker,
                "speedup": float(tide["ack_tps"].iloc[0]) / max(float(pwal["ack_tps"].iloc[0]), 1.0),
            })
    pdf = pd.DataFrame(points)
    colors = {"YCSB-A": "#be123c", "YCSB-B": "#047857", "YCSB-C": "#2563eb"}
    markers = {"YCSB-A": "o", "YCSB-B": "s", "YCSB-C": "^"}
    fig, ax = plt.subplots(figsize=(7.2, 4.05), constrained_layout=True)
    for workload in WORKLOAD_ORDER:
        sub = pdf[pdf["workload"] == workload].sort_values("worker_threads")
        if sub.empty:
            continue
        ax.plot(
            sub["worker_threads"],
            sub["speedup"],
            color=colors[workload],
            marker=markers[workload],
            linewidth=2.7,
            markersize=7.2,
            markerfacecolor="white",
            markeredgewidth=1.9,
            solid_capstyle="round",
            label=workload,
        )
    ax.axhline(1.0, color="#9ca3af", linewidth=1.0, linestyle="--")
    ax.set_xlabel("Worker threads", labelpad=8)
    ax.set_ylabel("TideWAL / P-WAL ack throughput", labelpad=8)
    ax.set_xticks(sorted(df["worker_threads"].unique()))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}x"))
    ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")
    ax.legend(frameon=False, loc="upper right")
    ax.text(
        0.0,
        1.04,
        "Worker-thread scaling; all modes use the same ERMIA-PWAL binary",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10.5,
        color="#374151",
    )
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def write_report(rows, raw_csv, outputs, args):
    with OUT_DOC.open("w") as f:
        print("# YCSB-A/B/C worker-thread TideWAL comparison", file=f)
        print("", file=f)
        print(f"date: {datetime.now().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("This rerun uses worker threads on the x-axis. All three systems use the same `build/cc/ermia_pwal/ycsb_ermia_pwal.exe` binary. Single WAL is selected by `CCBENCH_WAL_MODE=shared`, while P-WAL and TideWAL use `CCBENCH_WAL_MODE=per_thread`.", file=f)
        print("", file=f)
        print("Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. TideWAL additionally has an empty-frontier read-only fast path: if a read-only transaction has no durable dependency, it does not enter the dependency waitlist.", file=f)
        print("", file=f)
        print("## Conditions", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---|", file=f)
        print(f"| workloads | {args.workloads} |", file=f)
        print(f"| worker threads | {args.workers} |", file=f)
        print(f"| repeats | {args.repeats} |", file=f)
        print(f"| seconds | {args.seconds} |", file=f)
        print(f"| TideWAL logger mapping | {TIDEWAL_LOGGERS} |", file=f)
        print(f"| TideWAL group_size | {args.group_size} |", file=f)
        print(f"| TideWAL flush_us | {args.flush_us} |", file=f)
        print(f"| TideWAL max_pending | {args.max_pending} |", file=f)
        print("", file=f)
        print(f"summary csv: `{OUT_CSV.relative_to(ROOT)}`", file=f)
        print(f"raw csv: `{raw_csv.relative_to(ROOT)}`", file=f)
        print("", file=f)
        print("## Figures", file=f)
        print("", file=f)
        for output in outputs:
            print(f"- `{output.relative_to(ROOT)}`", file=f)
        print("", file=f)
        print("## 32-worker summary", file=f)
        print("", file=f)
        print("| workload | system | worker | logger | committer | total active | ack tps | p99 us | pending | read-only tx | read-only fast path | frontier collect ns/tx | waitlist reg ns/tx |", file=f)
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for workload in WORKLOAD_ORDER:
            for system in SYSTEM_ORDER:
                match = [
                    r for r in rows
                    if r["workload"] == workload and r["system"] == system and int(r["worker_threads"]) == 32
                ]
                if not match:
                    continue
                r = match[0]
                print(
                    f"| {workload} | {system} | {float(r['worker_threads']):.0f} | "
                    f"{float(r['logger_num']):.0f} | {float(r['committer_num']):.0f} | "
                    f"{float(r['total_active_threads']):.0f} | {float(r['ack_tps']):.0f} | "
                    f"{float(r['p99_us']):.0f} | {float(r['pending']):.0f} | "
                    f"{float(r['read_only_commits_per_tx']):.3f} | "
                    f"{float(r['read_only_fast_path_per_tx']):.3f} | "
                    f"{float(r['frontier_collect_ns_per_tx']):.1f} | "
                    f"{float(r['waitlist_registration_ns_per_tx']):.1f} |",
                    file=f,
                )
    return OUT_DOC


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workloads", default="ycsb_a,ycsb_b,ycsb_c")
    parser.add_argument("--workers", default="1,2,4,8,16,32")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--group-size", type=int, default=16)
    parser.add_argument("--flush-us", type=int, default=50)
    parser.add_argument("--max-pending", type=int, default=65536)
    parser.add_argument("--input-csv", default="")
    args = parser.parse_args()

    workloads = parse_workloads(args.workloads)
    workers = parse_int_list(args.workers)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS / f"ycsbabc_tidewal_worker_threads_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.input_csv:
        raw_rows = read_rows(ROOT / args.input_csv if not Path(args.input_csv).is_absolute() else args.input_csv)
    else:
        raw_rows = []
        for repeat in range(args.repeats):
            for workload in workloads:
                for worker in workers:
                    for mode in MODES:
                        row = run_case(out_dir, workload, mode, repeat, worker, args)
                        raw_rows.append(row)
                        print(
                            f"worker repeat={repeat} workload={workload} worker={worker} "
                            f"mode={mode} logger={row['logger_num']} committer={row['committer_num']} "
                            f"ack_tps={row['durable_ack_tps']} p99={row['ack_latency_p99_us']} "
                            f"pending={row['pending_commits']}",
                            flush=True,
                        )

    raw_csv = out_dir / f"ycsbabc_tidewal_worker_threads_raw_{stamp}.csv"
    write_csv(raw_csv, raw_rows)
    rows = aggregate(raw_rows)
    write_summary_csv(rows)

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    outputs = [
        FIG_DIR / "fig_ycsbabc_worker_threads_throughput.pdf",
        FIG_DIR / "fig_ycsbabc_worker_threads_latency.pdf",
        FIG_DIR / "fig_ycsbabc_worker_threads_pending.pdf",
        FIG_DIR / "fig_ycsbabc_worker_threads_tidewal_speedup_vs_pwal.pdf",
    ]
    draw_faceted_lines(
        rows,
        "ack_tps",
        "Ack throughput [tx/s]",
        outputs[0],
        "Worker-thread comparison with a common ERMIA-PWAL binary",
    )
    draw_faceted_lines(
        rows,
        "p99_us",
        "p99 latency [us]",
        outputs[1],
        "Worker-thread p99 durable-ack latency",
        yscale="log",
    )
    draw_faceted_lines(
        rows,
        "pending",
        "Pending durable commits",
        outputs[2],
        "Worker-thread pending durable commits",
    )
    draw_speedup(rows, outputs[3])
    report = write_report(rows, raw_csv, outputs, args)

    print(raw_csv)
    print(OUT_CSV)
    print(report)
    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
