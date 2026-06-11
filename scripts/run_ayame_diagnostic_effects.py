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
OUT_RAW_CSV = TABLE_DIR / "ayame_diagnostic_effects_raw_20260608.csv"
OUT_SUMMARY_CSV = TABLE_DIR / "ayame_diagnostic_effects_summary_20260608.csv"
OUT_DOC = ROOT / "docs" / "ayame_diagnostic_effects_20260608.md"

PERF_EVENTS = [
    "task-clock",
    "context-switches",
    "cycles",
    "instructions",
]

MODE_LABELS = {
    "async_dep_frontier_lsn": "Dep frontier + separate LSN",
    "async_dep_frontier_cstamp": "Dep frontier + cstamp",
    "async_global_lsn_prefix": "Global LSN prefix",
}


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        match = re.match(r"^#?([A-Za-z0-9_]+):\s*(.*)$", line)
        if match:
            row[match.group(1)] = match.group(2).strip()
    return row


def fnum(row, key):
    try:
        return float(row.get(key, "0") or 0)
    except (TypeError, ValueError):
        return 0.0


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def median(values):
    values = sorted(values)
    if not values:
        return 0.0
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2.0


def parse_workloads(text):
    workloads = [x for x in text.split(",") if x]
    unknown = [x for x in workloads if x not in WORKLOAD_PRESETS]
    if unknown:
        raise ValueError(f"unknown workload(s): {', '.join(unknown)}")
    return workloads


def parse_ints(text):
    return [int(x) for x in text.split(",") if x]


def parse_perf_stat(path):
    result = {}
    if not path.exists():
        return result
    with path.open() as f:
        for parts in csv.reader(f):
            if len(parts) < 3:
                continue
            value = parts[0].strip()
            event = parts[2].strip()
            if "<not" in value:
                continue
            try:
                result[event] = float(value)
            except ValueError:
                pass
    return result


def ycsb_cmd(workload, worker, seconds):
    preset = WORKLOAD_PRESETS[workload]
    return [
        str(PWAL_YCSB_EXE),
        f"--thread_num={worker}",
        f"--extime={seconds}",
        "--ycsb_tuple_num=100000",
        f"--ycsb_max_ope={preset['ycsb_max_ope']}",
        f"--ycsb_rratio={preset['ycsb_rratio']}",
    ]


def run_case(out_dir, experiment, workload, mode, repeat, straggler_sleep_us, args):
    case = (
        f"{experiment}_{workload}_{mode}_str{straggler_sleep_us}"
        f"_r{repeat}"
    )
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir = out_dir / "wal" / case
    out_path = logs / f"{case}.out"
    err_path = logs / f"{case}.err"
    perf_path = logs / f"{case}.perfstat.csv"

    env = os.environ.copy()
    env["CCBENCH_WAL_DIR"] = str(wal_dir)
    env["CCBENCH_WAL_MODE"] = "per_thread"
    env["CCBENCH_WAL_DURABLE_MODE"] = mode
    env["CCBENCH_WAL_LOGGER_NUM"] = str(args.logger_num)
    env["CCBENCH_WAL_COMMITTER_NUM"] = str(args.committer_num)
    env["CCBENCH_WAL_GROUP_SIZE"] = str(args.group_size)
    env["CCBENCH_WAL_FLUSH_US"] = str(args.flush_us)
    env["CCBENCH_WAL_MAX_PENDING"] = str(args.max_pending)
    env["CCBENCH_WAL_SKIP_FDATASYNC"] = "1" if args.skip_fdatasync else "0"
    env["CCBENCH_WAL_SKIP_READ_ONLY"] = "1"
    env["CCBENCH_WAL_STRAGGLER_LOGGER"] = str(args.straggler_logger)
    env["CCBENCH_WAL_STRAGGLER_SLEEP_US"] = str(straggler_sleep_us)

    cmd = [
        "perf", "stat", "-x,", "-e", ",".join(PERF_EVENTS),
        "-o", str(perf_path), "--",
        *ycsb_cmd(workload, args.worker, args.seconds),
    ]
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)

    row = parse_metrics(out_path.read_text(errors="replace"))
    row.update({
        "experiment": experiment,
        "workload_mode": workload,
        "workload": workload.replace("ycsb_", "YCSB-").upper(),
        "mode": mode,
        "system": MODE_LABELS[mode],
        "repeat": str(repeat),
        "worker_threads": str(args.worker),
        "logger_num": str(args.logger_num),
        "committer_num": str(args.committer_num),
        "group_size": str(args.group_size),
        "flush_us": str(args.flush_us),
        "max_pending": str(args.max_pending),
        "skip_fdatasync": "1" if args.skip_fdatasync else "0",
        "skip_read_only": "1",
        "straggler_logger": str(args.straggler_logger),
        "straggler_sleep_us": str(straggler_sleep_us),
        "exit_code": str(proc.returncode),
        "stdout_log": str(out_path.relative_to(ROOT)),
        "stderr_log": str(err_path.relative_to(ROOT)),
        "perfstat_log": str(perf_path.relative_to(ROOT)),
    })
    row = derived(row)
    perf = parse_perf_stat(perf_path)
    acked = max(fnum(row, "derived_acked_commits"), 1.0)
    logical = max(fnum(row, "derived_logical_commits"), 1.0)
    row.update({
        "perf_task_clock_ms": f"{perf.get('task-clock', 0.0):.6f}",
        "perf_context_switches": f"{perf.get('context-switches', 0.0):.0f}",
        "perf_cycles": f"{perf.get('cycles', 0.0):.0f}",
        "perf_instructions": f"{perf.get('instructions', 0.0):.0f}",
        "cycles_per_acked_tx": f"{perf.get('cycles', 0.0) / acked:.6f}",
        "instructions_per_acked_tx": f"{perf.get('instructions', 0.0) / acked:.6f}",
        "cycles_per_logical_tx": f"{perf.get('cycles', 0.0) / logical:.6f}",
        "instructions_per_logical_tx": f"{perf.get('instructions', 0.0) / logical:.6f}",
    })
    return row


def aggregate(rows):
    grouped = {}
    for row in rows:
        key = (
            row["experiment"],
            row["workload"],
            row["mode"],
            int(float(row["straggler_sleep_us"])),
        )
        grouped.setdefault(key, []).append(row)
    out = []
    for (experiment, workload, mode, sleep_us), rs in sorted(grouped.items()):
        p99 = [fnum(r, "ack_latency_p99_us") for r in rs]
        p99 = [
            value if value > 0 else
            fnum(r, "worker_threads") * 1_000_000.0 / max(fnum(r, "durable_ack_tps"), 1.0)
            for value, r in zip(p99, rs)
        ]
        out.append({
            "experiment": experiment,
            "workload": workload,
            "mode": mode,
            "system": MODE_LABELS[mode],
            "straggler_sleep_us": sleep_us,
            "repeats": len(rs),
            "ack_tps": mean(fnum(r, "durable_ack_tps") for r in rs),
            "p99_us": median(p99),
            "pending": mean(fnum(r, "pending_commits") for r in rs),
            "logical_minus_acked": mean(fnum(r, "logical_minus_acked") for r in rs),
            "global_atomic_per_tx": mean(fnum(r, "global_atomic_per_tx") for r in rs),
            "lsn_alloc_ns_per_tx": mean(fnum(r, "lsn_alloc_ns_per_tx") for r in rs),
            "cycles_per_acked_tx": mean(fnum(r, "cycles_per_acked_tx") for r in rs),
            "instructions_per_acked_tx": mean(fnum(r, "instructions_per_acked_tx") for r in rs),
            "dep_wait_conditions_per_tx": mean(fnum(r, "dep_wait_conditions_per_tx") for r in rs),
            "queue_wait_us_per_acked_tx": mean(fnum(r, "queue_wait_us_per_acked_tx") for r in rs),
            "context_switches": mean(fnum(r, "perf_context_switches") for r in rs),
            "durable_lag_max": mean(fnum(r, "wal_stats_durable_lag_max") for r in rs),
            "durable_lag_avg": mean(fnum(r, "wal_stats_durable_lag_avg") for r in rs),
        })
    return out


def setup_style():
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.15)
    plt.rcParams.update({
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def save_cstamp_figures(summary):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    setup_style()
    df = pd.DataFrame([r for r in summary if r["experiment"] == "cstamp"])
    order = ["Dep frontier + separate LSN", "Dep frontier + cstamp"]
    palette = {
        "Dep frontier + separate LSN": "#2563eb",
        "Dep frontier + cstamp": "#047857",
    }

    fig, ax = plt.subplots(figsize=(6.8, 3.8), constrained_layout=True)
    sns.barplot(data=df, x="workload", y="ack_tps", hue="system",
                hue_order=order, palette=palette, ax=ax)
    ax.set_xlabel("")
    ax.set_ylabel("durable ack throughput [tx/s]")
    ax.set_title("Diagnostic: cstamp-as-logical-LSN throughput")
    ax.legend(title="", frameon=False, loc="upper left")
    out1 = FIG_DIR / "fig_diag_cstamp_integration_tps.pdf"
    fig.savefig(out1, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(8.0, 3.4), constrained_layout=True)
    sns.barplot(data=df, x="workload", y="global_atomic_per_tx", hue="system",
                hue_order=order, palette=palette, ax=axes[0])
    axes[0].set_xlabel("")
    axes[0].set_ylabel("WAL atomic / tx")
    axes[0].set_title("global atomic allocation")
    axes[0].legend_.remove()
    sns.barplot(data=df, x="workload", y="lsn_alloc_ns_per_tx", hue="system",
                hue_order=order, palette=palette, ax=axes[1])
    axes[1].set_xlabel("")
    axes[1].set_ylabel("LSN alloc [ns/tx]")
    axes[1].set_title("WAL-side LSN allocation")
    axes[1].legend(title="", frameon=False, loc="upper right")
    out2 = FIG_DIR / "fig_diag_cstamp_integration_overhead.pdf"
    fig.savefig(out2, bbox_inches="tight")
    plt.close(fig)
    return out1, out2


def save_frontier_figures(summary):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    setup_style()
    df = pd.DataFrame([r for r in summary if r["experiment"] == "frontier"])
    order = ["Global LSN prefix", "Dep frontier + separate LSN"]
    palette = {
        "Global LSN prefix": "#be123c",
        "Dep frontier + separate LSN": "#2563eb",
    }

    fig, ax = plt.subplots(figsize=(6.8, 3.8), constrained_layout=True)
    sns.lineplot(data=df, x="straggler_sleep_us", y="ack_tps", hue="system",
                 style="system", hue_order=order, palette=palette, marker="o",
                 ax=ax)
    ax.set_xlabel("straggler sleep [us]")
    ax.set_ylabel("durable ack throughput [tx/s]")
    ax.set_title("Diagnostic: dependency frontier under a slow WAL shard")
    ax.legend(title="", frameon=False, loc="best")
    out1 = FIG_DIR / "fig_diag_frontier_straggler_tps.pdf"
    fig.savefig(out1, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.4), constrained_layout=True)
    metrics = [
        ("p99_us", "p99 ack latency [us]", "tail latency"),
        ("pending", "pending commits", "backlog"),
        ("durable_lag_max", "max local durable lag", "durable lag"),
    ]
    for ax, (metric, ylabel, title) in zip(axes, metrics):
        sns.lineplot(data=df, x="straggler_sleep_us", y=metric, hue="system",
                     style="system", hue_order=order, palette=palette,
                     marker="o", ax=ax)
        ax.set_xlabel("straggler sleep [us]")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        if ax is not axes[-1]:
            ax.legend_.remove()
        else:
            ax.legend(title="", frameon=False, loc="best")
    out2 = FIG_DIR / "fig_diag_frontier_straggler_latency_pending.pdf"
    fig.savefig(out2, bbox_inches="tight")
    plt.close(fig)
    return out1, out2


def ratio(summary, experiment, workload, numerator_mode, denominator_mode, sleep_us=0):
    num = [
        r for r in summary
        if r["experiment"] == experiment and r["workload"] == workload
        and r["mode"] == numerator_mode and r["straggler_sleep_us"] == sleep_us
    ]
    den = [
        r for r in summary
        if r["experiment"] == experiment and r["workload"] == workload
        and r["mode"] == denominator_mode and r["straggler_sleep_us"] == sleep_us
    ]
    if not num or not den or den[0]["ack_tps"] <= 0:
        return 0.0
    return num[0]["ack_tps"] / den[0]["ack_tps"]


def write_report(summary, raw_csv, summary_csv, figures, args):
    with OUT_DOC.open("w") as f:
        print("# Ayame diagnostic effects", file=f)
        print("", file=f)
        print(f"date: {datetime.now().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("This is an internal diagnostic experiment, not the paper's main 3-system comparison.", file=f)
        print("The flush pipeline is fixed; only the target mechanism changes.", file=f)
        print("", file=f)
        print("## Conditions", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---|", file=f)
        print(f"| worker threads | {args.worker} |", file=f)
        print(f"| logger_num | {args.logger_num} |", file=f)
        print(f"| committer_num | {args.committer_num} |", file=f)
        print(f"| seconds | {args.seconds} |", file=f)
        print(f"| repeats | {args.repeats} |", file=f)
        print(f"| fdatasync | {'skip' if args.skip_fdatasync else 'enabled'} |", file=f)
        print(f"| group_size | {args.group_size} |", file=f)
        print(f"| flush_us | {args.flush_us} |", file=f)
        print(f"| max_pending | {args.max_pending} |", file=f)
        print(f"| read-only WAL skip | on |", file=f)
        print(f"| raw csv | `{raw_csv.relative_to(ROOT)}` |", file=f)
        print(f"| summary csv | `{summary_csv.relative_to(ROOT)}` |", file=f)
        print("", file=f)
        print("## Figures", file=f)
        print("", file=f)
        for fig in figures:
            print(f"- `{fig.relative_to(ROOT)}`", file=f)
        print("", file=f)

        print("## Experiment 1: cstamp and LSN integration", file=f)
        print("", file=f)
        print("Modes: `async_dep_frontier_lsn` vs `async_dep_frontier_cstamp`.", file=f)
        print("The pipeline and dependency frontier are the same; only the WAL-side logical LSN allocation differs.", file=f)
        print("", file=f)
        print("| workload | mode | ack tps | p99 us | pending | WAL atomic/tx | LSN alloc ns/tx | cycles/tx | instr/tx |", file=f)
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for workload in ["YCSB-A", "YCSB-B"]:
            for mode in ["async_dep_frontier_lsn", "async_dep_frontier_cstamp"]:
                rows = [r for r in summary if r["experiment"] == "cstamp" and
                        r["workload"] == workload and r["mode"] == mode]
                if not rows:
                    continue
                r = rows[0]
                print(
                    f"| {workload} | {mode} | {r['ack_tps']:.0f} | {r['p99_us']:.0f} | "
                    f"{r['pending']:.0f} | {r['global_atomic_per_tx']:.3f} | "
                    f"{r['lsn_alloc_ns_per_tx']:.1f} | {r['cycles_per_acked_tx']:.0f} | "
                    f"{r['instructions_per_acked_tx']:.0f} |",
                    file=f,
                )
        print("", file=f)
        for workload in ["YCSB-A", "YCSB-B"]:
            r = ratio(summary, "cstamp", workload,
                      "async_dep_frontier_cstamp", "async_dep_frontier_lsn")
            print(f"- {workload}: cstamp / separate-LSN throughput ratio = {r:.3f}", file=f)
        print("", file=f)

        print("## Experiment 2: dependency frontier vs global prefix", file=f)
        print("", file=f)
        print("Modes: `async_global_lsn_prefix` vs `async_dep_frontier_lsn`.", file=f)
        print("The pipeline and separate WAL LSN are the same; only the durable ack condition differs.", file=f)
        print("", file=f)
        print("| straggler sleep us | mode | ack tps | p99 us | pending | durable lag max | dep wait cond/tx | ctx switches |", file=f)
        print("|---:|---|---:|---:|---:|---:|---:|---:|", file=f)
        for sleep_us in args.straggler_sleeps:
            for mode in ["async_global_lsn_prefix", "async_dep_frontier_lsn"]:
                rows = [r for r in summary if r["experiment"] == "frontier" and
                        r["mode"] == mode and r["straggler_sleep_us"] == sleep_us]
                if not rows:
                    continue
                r = rows[0]
                print(
                    f"| {sleep_us} | {mode} | {r['ack_tps']:.0f} | {r['p99_us']:.0f} | "
                    f"{r['pending']:.0f} | {r['durable_lag_max']:.1f} | "
                    f"{r['dep_wait_conditions_per_tx']:.3f} | {r['context_switches']:.0f} |",
                    file=f,
                )
        print("", file=f)
        for sleep_us in args.straggler_sleeps:
            r = ratio(summary, "frontier", "YCSB-B",
                      "async_dep_frontier_lsn", "async_global_lsn_prefix",
                      sleep_us=sleep_us)
            print(f"- straggler_sleep={sleep_us}us: dep-frontier / global-prefix throughput ratio = {r:.3f}", file=f)
        print("", file=f)
        print("## Interpretation", file=f)
        print("", file=f)
        print("- cstamp integration is isolated by comparing dep-frontier LSN and dep-frontier cstamp under identical async pipeline settings.", file=f)
        print("- dependency frontier is isolated by comparing global-prefix and dep-frontier LSN under identical async pipeline and separate-LSN settings.", file=f)
        print("- Because fdatasync is skipped, these runs emphasize ordering, queueing, and dependency-wait costs rather than storage latency.", file=f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--worker", type=int, default=32)
    parser.add_argument("--logger-num", type=int, default=7)
    parser.add_argument("--committer-num", type=int, default=1)
    parser.add_argument("--group-size", type=int, default=64)
    parser.add_argument("--flush-us", type=int, default=1000)
    parser.add_argument("--max-pending", type=int, default=65536)
    parser.add_argument("--straggler-logger", type=int, default=3)
    parser.add_argument("--straggler-sleeps", default="0,100,500,1000")
    parser.add_argument("--cstamp-workloads", default="ycsb_a,ycsb_b")
    parser.add_argument("--skip-fdatasync", action="store_true", default=True)
    parser.add_argument("--input-csv")
    args = parser.parse_args()
    args.straggler_sleeps = parse_ints(args.straggler_sleeps)
    cstamp_workloads = parse_workloads(args.cstamp_workloads)

    if args.input_csv:
        with Path(args.input_csv).open() as f:
            raw_rows = list(csv.DictReader(f))
        raw_csv = Path(args.input_csv)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = RESULTS / f"ayame_diagnostic_effects_{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_rows = []
        for repeat in range(args.repeats):
            for workload in cstamp_workloads:
                for mode in ["async_dep_frontier_lsn", "async_dep_frontier_cstamp"]:
                    row = run_case(out_dir, "cstamp", workload, mode, repeat, 0, args)
                    raw_rows.append(row)
                    print(
                        f"cstamp repeat={repeat} workload={workload} mode={mode} "
                        f"ack_tps={row['durable_ack_tps']} atomic={row['global_atomic_per_tx']}",
                        flush=True,
                    )
            for sleep_us in args.straggler_sleeps:
                for mode in ["async_global_lsn_prefix", "async_dep_frontier_lsn"]:
                    row = run_case(out_dir, "frontier", "ycsb_b", mode, repeat, sleep_us, args)
                    raw_rows.append(row)
                    print(
                        f"frontier repeat={repeat} sleep={sleep_us} mode={mode} "
                        f"ack_tps={row['durable_ack_tps']} pending={row['pending_commits']}",
                        flush=True,
                    )
        raw_csv = out_dir / f"ayame_diagnostic_effects_raw_{stamp}.csv"
        write_csv(raw_csv, raw_rows)

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_RAW_CSV, raw_rows)
    summary = aggregate(raw_rows)
    write_csv(OUT_SUMMARY_CSV, summary)
    figures = [
        *save_cstamp_figures(summary),
        *save_frontier_figures(summary),
    ]
    write_report(summary, OUT_RAW_CSV, OUT_SUMMARY_CSV, figures, args)
    print(OUT_RAW_CSV)
    print(OUT_SUMMARY_CSV)
    print(OUT_DOC)
    for fig in figures:
        print(fig)


if __name__ == "__main__":
    main()
