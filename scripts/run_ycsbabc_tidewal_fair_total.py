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
OUT_CSV = TABLE_DIR / "ycsbabc_tidewal_fair_total_20260607.csv"

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

TOTAL_ALLOC = {
    4: (2, 1, 1),
    8: (5, 2, 1),
    16: (12, 3, 1),
    24: (18, 5, 1),
    32: (24, 7, 1),
    48: (38, 9, 1),
    96: (76, 19, 1),
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


def tidewal_alloc(total):
    if total in TOTAL_ALLOC:
        return TOTAL_ALLOC[total]
    if total < 4:
        return None
    committer = 1
    logger = max(1, round((total - committer) / 5))
    worker = total - logger - committer
    if worker < 1:
        return None
    return worker, logger, committer


def mode_allocation(mode, total):
    if mode == "single_wal":
        return total, 0, 0
    if mode == "pwal":
        return total, total, 0
    if mode == "tidewal":
        return tidewal_alloc(total)
    raise ValueError(f"unknown mode: {mode}")


def run_case(out_dir, workload, mode, repeat, total, args):
    alloc = mode_allocation(mode, total)
    if alloc is None:
        return None
    worker, logger, committer = alloc
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir = out_dir / "wal"
    preset = WORKLOAD_PRESETS[workload]

    env = os.environ.copy()
    env["CCBENCH_WAL_DIR"] = str(wal_dir)
    env["CCBENCH_WAL_SKIP_READ_ONLY"] = "1"
    if mode == "single_wal":
        exe = WAL_YCSB_EXE
    else:
        exe = PWAL_YCSB_EXE
        env["CCBENCH_WAL_LOGGER_NUM"] = str(logger)
        env["CCBENCH_WAL_COMMITTER_NUM"] = str(committer if committer else 1)
        env["CCBENCH_WAL_GROUP_SIZE"] = str(args.group_size)
        env["CCBENCH_WAL_FLUSH_US"] = str(args.flush_us)
        env["CCBENCH_WAL_MAX_PENDING"] = str(args.max_pending)
        if mode == "pwal":
            env["CCBENCH_WAL_DURABLE_MODE"] = "sync"
        elif mode == "tidewal":
            env["CCBENCH_WAL_DURABLE_MODE"] = "async_dep_frontier_cstamp"

    cmd = [
        str(exe),
        f"--thread_num={worker}",
        f"--extime={args.seconds}",
        "--ycsb_tuple_num=100000",
        f"--ycsb_max_ope={preset['ycsb_max_ope']}",
        f"--ycsb_rratio={preset['ycsb_rratio']}",
    ]
    label = f"{workload}_{mode}_total{total}_w{worker}_l{logger}_c{committer}_r{repeat}"
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
            "total_threads": str(total),
            "seconds": str(args.seconds),
            "ycsb_max_ope": str(preset["ycsb_max_ope"]),
            "ycsb_rratio": str(preset["ycsb_rratio"]),
            "group_size": str(args.group_size if mode == "tidewal" else 0),
            "flush_us": str(args.flush_us if mode == "tidewal" else 0),
            "max_pending": str(args.max_pending if mode == "tidewal" else 0),
            "skip_read_only": "1",
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
            int(row["total_threads"]),
        )
        grouped.setdefault(key, []).append(row)
    out = []
    for (workload_mode, workload, system, total), rs in sorted(grouped.items()):
        p99_values = []
        for r in rs:
            p99 = safe_float(r.get("ack_latency_p99_us"))
            if p99 <= 0:
                workers = max(safe_float(r.get("worker_threads")), 1.0)
                p99 = workers * 1_000_000.0 / max(safe_float(r.get("durable_ack_tps")), 1.0)
            p99_values.append(p99)
        out.append(
            {
                "workload_mode": workload_mode,
                "workload": workload,
                "system": system,
                "total_threads": total,
                "worker_threads": median(safe_float(r.get("worker_threads")) for r in rs),
                "logger_num": median(safe_float(r.get("logger_num")) for r in rs),
                "committer_num": median(safe_float(r.get("committer_num")) for r in rs),
                "ack_tps": mean(safe_float(r.get("durable_ack_tps")) for r in rs),
                "p99_us": median(p99_values),
                "pending": mean(safe_float(r.get("pending_commits")) for r in rs),
                "read_only_commits_per_tx": mean(safe_float(r.get("wal_stats_read_only_commits")) / max(safe_float(r.get("wal_stats_commits")), 1.0) for r in rs),
                "frontier_collect_ns_per_tx": mean(safe_float(r.get("frontier_collect_ns_per_tx")) for r in rs),
                "frontier_publish_ns_per_tx": mean(safe_float(r.get("frontier_publish_ns_per_tx")) for r in rs),
                "fdatasync_ns_per_tx": mean(safe_float(r.get("fdatasync_ns_per_tx")) for r in rs),
            }
        )
    return out


def write_summary_csv(rows):
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "workload_mode",
        "workload",
        "system",
        "total_threads",
        "worker_threads",
        "logger_num",
        "committer_num",
        "ack_tps",
        "p99_us",
        "pending",
        "read_only_commits_per_tx",
        "frontier_collect_ns_per_tx",
        "frontier_publish_ns_per_tx",
        "fdatasync_ns_per_tx",
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
    fig, axes = plt.subplots(1, 3, figsize=(10.6, 3.7), sharey=False, constrained_layout=True)
    for ax, workload in zip(axes, WORKLOAD_ORDER):
        sub_w = df[df["workload"] == workload]
        for system in SYSTEM_ORDER:
            sub = sub_w[sub_w["system"] == system].sort_values("total_threads")
            if sub.empty:
                continue
            ax.plot(
                sub["total_threads"],
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
        ax.set_xlabel("Total active threads")
        ax.set_xticks(sorted(df["total_threads"].unique()))
        ax.set_xticklabels([str(int(x)) for x in sorted(df["total_threads"].unique())], rotation=0)
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
        for total in sorted(df["total_threads"].unique()):
            pwal = df[(df["workload"] == workload) & (df["system"] == "P-WAL") & (df["total_threads"] == total)]
            tide = df[(df["workload"] == workload) & (df["system"] == "TideWAL") & (df["total_threads"] == total)]
            if pwal.empty or tide.empty:
                continue
            points.append({
                "workload": workload,
                "total_threads": total,
                "speedup": float(tide["ack_tps"].iloc[0]) / max(float(pwal["ack_tps"].iloc[0]), 1.0),
            })
    pdf = pd.DataFrame(points)
    colors = {"YCSB-A": "#be123c", "YCSB-B": "#047857", "YCSB-C": "#2563eb"}
    markers = {"YCSB-A": "o", "YCSB-B": "s", "YCSB-C": "^"}
    fig, ax = plt.subplots(figsize=(7.2, 4.05), constrained_layout=True)
    for workload in WORKLOAD_ORDER:
        sub = pdf[pdf["workload"] == workload].sort_values("total_threads")
        if sub.empty:
            continue
        ax.plot(
            sub["total_threads"],
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
    ax.set_xlabel("Total active threads", labelpad=8)
    ax.set_ylabel("TideWAL / P-WAL ack throughput", labelpad=8)
    ax.set_xticks(sorted(df["total_threads"].unique()))
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
        "Fair total-thread-budget speedup with read-only WAL skip enabled for all systems",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10.5,
        color="#374151",
    )
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def write_report(rows, raw_csv, outputs, args):
    path = ROOT / "docs" / "ycsbabc_tidewal_fair_total_20260607.md"
    with path.open("w") as f:
        print("# YCSB-A/B/C fair total-thread TideWAL comparison", file=f)
        print("", file=f)
        print(f"date: {datetime.now().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("This rerun uses `total active threads = worker + flusher/logger + committer` for TideWAL. Single WAL and P-WAL use no background durability threads, so their worker count equals total active threads.", file=f)
        print("", file=f)
        print("Read-only WAL skip is enabled for every WAL system with `CCBENCH_WAL_SKIP_READ_ONLY=1`. For Single WAL and P-WAL this skips WAL records for read-only transactions. TideWAL keeps its read-only dependency wait semantics, but still skips WAL append and frontier publish for read-only transactions.", file=f)
        print("", file=f)
        print("## Conditions", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---|", file=f)
        print(f"| workloads | {args.workloads} |", file=f)
        print(f"| total active threads | {args.totals} |", file=f)
        print(f"| repeats | {args.repeats} |", file=f)
        print(f"| seconds | {args.seconds} |", file=f)
        print(f"| TideWAL group_size | {args.group_size} |", file=f)
        print(f"| TideWAL flush_us | {args.flush_us} |", file=f)
        print(f"| TideWAL max_pending | {args.max_pending} |", file=f)
        print("", file=f)
        print(f"raw csv: `{raw_csv.relative_to(ROOT)}`", file=f)
        print(f"summary csv: `{OUT_CSV.relative_to(ROOT)}`", file=f)
        print("", file=f)
        print("## Figures", file=f)
        print("", file=f)
        for output in outputs:
            print(f"- `{output.relative_to(ROOT)}`", file=f)
        print("", file=f)
        print("## 32 total-thread summary", file=f)
        print("", file=f)
        print("| workload | system | worker | logger | committer | ack tps | p99 us | pending | read-only tx ratio |", file=f)
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for workload in WORKLOAD_ORDER:
            for system in SYSTEM_ORDER:
                match = [
                    r for r in rows
                    if r["workload"] == workload and r["system"] == system and int(r["total_threads"]) == 32
                ]
                if not match:
                    continue
                r = match[0]
                print(
                    f"| {workload} | {system} | {float(r['worker_threads']):.0f} | "
                    f"{float(r['logger_num']):.0f} | {float(r['committer_num']):.0f} | "
                    f"{float(r['ack_tps']):.0f} | {float(r['p99_us']):.0f} | "
                    f"{float(r['pending']):.0f} | {float(r['read_only_commits_per_tx']):.3f} |",
                    file=f,
                )
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workloads", default="ycsb_a,ycsb_b,ycsb_c")
    parser.add_argument("--totals", default="1,2,4,8,16,32")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--group-size", type=int, default=16)
    parser.add_argument("--flush-us", type=int, default=50)
    parser.add_argument("--max-pending", type=int, default=65536)
    parser.add_argument("--input-csv", default="")
    args = parser.parse_args()

    workloads = parse_workloads(args.workloads)
    totals = parse_int_list(args.totals)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS / f"ycsbabc_tidewal_fair_total_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.input_csv:
        raw_rows = read_rows(ROOT / args.input_csv if not Path(args.input_csv).is_absolute() else args.input_csv)
    else:
        raw_rows = []
        for repeat in range(args.repeats):
            for workload in workloads:
                for total in totals:
                    for mode in MODES:
                        row = run_case(out_dir, workload, mode, repeat, total, args)
                        if row is None:
                            print(f"fair skip repeat={repeat} workload={workload} total={total} mode={mode}")
                            continue
                        raw_rows.append(row)
                        print(
                            f"fair repeat={repeat} workload={workload} total={total} "
                            f"mode={mode} worker={row['worker_threads']} "
                            f"logger={row['logger_num']} committer={row['committer_num']} "
                            f"ack_tps={row['durable_ack_tps']} p99={row['ack_latency_p99_us']} "
                            f"pending={row['pending_commits']}",
                            flush=True,
                        )

    raw_csv = out_dir / f"ycsbabc_tidewal_fair_total_raw_{stamp}.csv"
    write_csv(raw_csv, raw_rows)
    rows = aggregate(raw_rows)
    write_summary_csv(rows)

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    outputs = [
        FIG_DIR / "fig_ycsbabc_fair_total_throughput.pdf",
        FIG_DIR / "fig_ycsbabc_fair_total_latency.pdf",
        FIG_DIR / "fig_ycsbabc_fair_total_pending.pdf",
        FIG_DIR / "fig_ycsbabc_fair_tidewal_speedup_vs_pwal.pdf",
    ]
    draw_faceted_lines(
        rows,
        "ack_tps",
        "Ack throughput [tx/s]",
        outputs[0],
        "Fair total-thread-budget comparison with read-only WAL skip enabled for all systems",
    )
    draw_faceted_lines(
        rows,
        "p99_us",
        "p99 latency [us]",
        outputs[1],
        "Fair total-thread-budget p99 durable-ack latency",
        yscale="log",
    )
    draw_faceted_lines(
        rows,
        "pending",
        "Pending durable commits",
        outputs[2],
        "Fair total-thread-budget pending durable commits",
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
