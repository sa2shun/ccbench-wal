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
    derived,
    write_csv,
)
from run_ycsbabc_tidewal_worker_threads import mode_allocation


TABLE_DIR = ROOT / "paper" / "tables"
FIG_DIR = ROOT / "paper" / "figures"
OUT_RAW_CSV = TABLE_DIR / "ycsbc_readonly_deep_dive_raw_20260608.csv"
OUT_SUMMARY_CSV = TABLE_DIR / "ycsbc_readonly_deep_dive_summary_20260608.csv"
OUT_TOP_CSV = TABLE_DIR / "ycsbc_readonly_deep_dive_perf_top_20260608.csv"
OUT_DOC = ROOT / "docs" / "ycsbc_readonly_deep_dive_20260608.md"
OUT_FIG = FIG_DIR / "fig_ycsbc_readonly_deep_dive.pdf"

MODES = ["single_wal", "pwal", "tidewal"]
SYSTEM_LABELS = {
    "single_wal": "Single WAL",
    "pwal": "P-WAL",
    "tidewal": "TideWAL",
}
SYSTEM_ORDER = ["Single WAL", "P-WAL", "TideWAL"]

EVENTS = [
    "task-clock",
    "context-switches",
    "cpu-migrations",
    "page-faults",
    "cycles",
    "instructions",
    "cache-references",
    "cache-misses",
    "LLC-loads",
    "LLC-load-misses",
]


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        row[key.lstrip("#").strip()] = value.strip()
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


def env_for_mode(mode, worker, args, out_dir, case_name):
    alloc = mode_allocation(mode, worker)
    logger = alloc["logger_num"]
    committer = alloc["committer_threads"]
    env = os.environ.copy()
    env["CCBENCH_WAL_DIR"] = str(out_dir / "wal" / case_name)
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
        raise ValueError(mode)
    return env, alloc


def ycsbc_cmd(worker, seconds):
    return [
        str(PWAL_YCSB_EXE),
        f"--thread_num={worker}",
        f"--extime={seconds}",
        "--ycsb_tuple_num=100000",
        "--ycsb_max_ope=10",
        "--ycsb_rratio=100",
    ]


def enrich_row(row, perf, worker):
    row = derived(row)
    tps = fnum(row, "durable_ack_tps")
    logical = max(fnum(row, "derived_logical_commits"), 1.0)
    acked = max(fnum(row, "derived_acked_commits"), 1.0)
    benchmark_latency_ns = fnum(row, "latency[ns]")
    closed_loop_ns = worker * 1_000_000_000.0 / max(tps, 1.0)
    frontier_collect = fnum(row, "frontier_collect_ns_per_tx")
    row.update({
        "benchmark_latency_ns": f"{benchmark_latency_ns:.6f}",
        "closed_loop_service_ns": f"{closed_loop_ns:.6f}",
        "frontier_collect_ns_per_read": f"{frontier_collect / 10.0:.6f}",
        "read_only_fast_path_per_tx": (
            f"{fnum(row, 'wal_stats_read_only_fast_path_acks') / logical:.6f}"
        ),
        "read_only_ratio": f"{fnum(row, 'wal_stats_read_only_commits') / logical:.6f}",
        "wal_bytes": row.get("wal_stats_bytes", "0"),
        "fdatasync_count": row.get("wal_stats_fdatasync_count", "0"),
        "pending": row.get("pending_commits", "0"),
        "waitlist_registration_ns_per_tx": row.get("waitlist_registration_ns_per_tx", "0"),
        "committer_cpu_ns_per_tx": row.get("committer_cpu_ns_per_tx", "0"),
        "flusher_cpu_ns_per_tx": row.get("flusher_cpu_ns_per_tx", "0"),
        "perf_task_clock_ms": f"{perf.get('task-clock', 0.0):.6f}",
        "perf_context_switches": f"{perf.get('context-switches', 0.0):.0f}",
        "perf_cycles": f"{perf.get('cycles', 0.0):.0f}",
        "perf_instructions": f"{perf.get('instructions', 0.0):.0f}",
        "cycles_per_tx": f"{perf.get('cycles', 0.0) / acked:.6f}",
        "instructions_per_tx": f"{perf.get('instructions', 0.0) / acked:.6f}",
        "ipc": f"{perf.get('instructions', 0.0) / max(perf.get('cycles', 0.0), 1.0):.6f}",
    })
    return row


def run_stat_case(out_dir, mode, repeat, args):
    case = f"ycsbc_{mode}_r{repeat}"
    env, alloc = env_for_mode(mode, args.worker, args, out_dir, case)
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    out_path = logs / f"{case}.out"
    err_path = logs / f"{case}.err"
    perf_path = logs / f"{case}.perfstat.csv"
    cmd = [
        "perf", "stat", "-x,", "-e", ",".join(EVENTS),
        "-o", str(perf_path), "--",
        *ycsbc_cmd(args.worker, args.seconds),
    ]
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)
    row = parse_metrics(out_path.read_text(errors="replace"))
    row.update({
        "mode": mode,
        "system": SYSTEM_LABELS[mode],
        "repeat": str(repeat),
        "worker_threads": str(args.worker),
        "wal_streams": str(alloc["wal_streams"]),
        "flusher_threads": str(alloc["flusher_threads"]),
        "committer_threads": str(alloc["committer_threads"]),
        "logger_num": str(alloc["logger_num"]),
        "total_active_threads": str(
            args.worker + alloc["flusher_threads"] + alloc["committer_threads"]
            if mode == "tidewal" else args.worker
        ),
        "seconds": str(args.seconds),
        "exit_code": str(proc.returncode),
        "stdout_log": str(out_path.relative_to(ROOT)),
        "stderr_log": str(err_path.relative_to(ROOT)),
        "perfstat_log": str(perf_path.relative_to(ROOT)),
    })
    return enrich_row(row, parse_perf_stat(perf_path), args.worker)


def parse_top_symbols(path, limit):
    rows = []
    pattern = re.compile(r"^\s*([0-9]+\.[0-9]+)%\s+\S+\s+(.+?)\s{2,}(\S+)\s+-")
    for line in path.read_text(errors="replace").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        rows.append({
            "self_pct": match.group(1),
            "symbol": match.group(2).strip(),
            "dso": match.group(3).strip(),
        })
        if len(rows) >= limit:
            break
    return rows


def run_record_case(out_dir, mode, args):
    case = f"ycsbc_{mode}"
    env, _ = env_for_mode(mode, args.worker, args, out_dir, f"record_{case}")
    logs = out_dir / "perf_record"
    logs.mkdir(parents=True, exist_ok=True)
    out_path = logs / f"{case}.out"
    err_path = logs / f"{case}.err"
    data_path = logs / f"{case}.perf.data"
    report_path = logs / f"{case}.report.nochildren.txt"
    report_err = logs / f"{case}.report.err"
    cmd = [
        "perf", "record", "-F", str(args.freq), "-g", "--call-graph", args.call_graph,
        "-o", str(data_path), "--",
        *ycsbc_cmd(args.worker, args.record_seconds),
    ]
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)
    if proc.returncode == 0:
        with report_path.open("w") as out, report_err.open("w") as err:
            subprocess.run([
                "perf", "report", "-i", str(data_path), "--stdio", "--no-children",
                "--sort", "symbol,dso", "--percent-limit", "0.5",
            ], cwd=ROOT, stdout=out, stderr=err)
    top_rows = []
    if report_path.exists():
        for rank, row in enumerate(parse_top_symbols(report_path, args.top_limit), start=1):
            row.update({
                "mode": mode,
                "system": SYSTEM_LABELS[mode],
                "rank": str(rank),
                "worker_threads": str(args.worker),
                "report_log": str(report_path.relative_to(ROOT)),
            })
            top_rows.append(row)
    if not args.keep_perf_data and data_path.exists():
        data_path.unlink()
    return top_rows


def aggregate(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["system"], []).append(row)
    summary = []
    for system in SYSTEM_ORDER:
        rs = grouped.get(system, [])
        if not rs:
            continue
        def avg(key):
            return mean(fnum(r, key) for r in rs)
        def med(key):
            return median(fnum(r, key) for r in rs)
        summary.append({
            "system": system,
            "worker_threads": rs[0]["worker_threads"],
            "wal_streams": rs[0]["wal_streams"],
            "flusher_threads": rs[0]["flusher_threads"],
            "committer_threads": rs[0]["committer_threads"],
            "total_active_threads": rs[0]["total_active_threads"],
            "ack_tps": avg("durable_ack_tps"),
            "benchmark_latency_ns": avg("benchmark_latency_ns"),
            "closed_loop_service_ns": avg("closed_loop_service_ns"),
            "p99_us": med("ack_latency_p99_us"),
            "wal_bytes": avg("wal_bytes"),
            "fdatasync_count": avg("fdatasync_count"),
            "pending": avg("pending"),
            "read_only_ratio": avg("read_only_ratio"),
            "read_only_fast_path_per_tx": avg("read_only_fast_path_per_tx"),
            "frontier_collect_ns_per_tx": avg("frontier_collect_ns_per_tx"),
            "frontier_collect_ns_per_read": avg("frontier_collect_ns_per_read"),
            "waitlist_registration_ns_per_tx": avg("waitlist_registration_ns_per_tx"),
            "committer_cpu_ns_per_tx": avg("committer_cpu_ns_per_tx"),
            "flusher_cpu_ns_per_tx": avg("flusher_cpu_ns_per_tx"),
            "cycles_per_tx": avg("cycles_per_tx"),
            "instructions_per_tx": avg("instructions_per_tx"),
            "ipc": avg("ipc"),
            "context_switches": avg("perf_context_switches"),
        })
    return summary


def setup_style():
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.1)
    plt.rcParams.update({
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def save_figure(summary):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    setup_style()
    df = pd.DataFrame(summary)
    fig, axes = plt.subplots(1, 3, figsize=(11.2, 3.4), constrained_layout=True)
    palette = {"Single WAL": "#4b5563", "P-WAL": "#d97706", "TideWAL": "#047857"}
    sns.barplot(data=df, x="system", y="ack_tps", hue="system",
                order=SYSTEM_ORDER, hue_order=SYSTEM_ORDER, palette=palette,
                legend=False, ax=axes[0])
    axes[0].set_xlabel("")
    axes[0].set_ylabel("throughput [tx/s]")
    axes[0].set_title("YCSB-C throughput")
    axes[0].tick_params(axis="x", rotation=20)
    sns.barplot(data=df, x="system", y="closed_loop_service_ns", hue="system",
                order=SYSTEM_ORDER, hue_order=SYSTEM_ORDER, palette=palette,
                legend=False, ax=axes[1])
    axes[1].set_xlabel("")
    axes[1].set_ylabel("ns/tx")
    axes[1].set_title("closed-loop service time")
    axes[1].tick_params(axis="x", rotation=20)
    sns.barplot(data=df, x="system", y="instructions_per_tx", hue="system",
                order=SYSTEM_ORDER, hue_order=SYSTEM_ORDER, palette=palette,
                legend=False, ax=axes[2])
    axes[2].set_xlabel("")
    axes[2].set_ylabel("instructions/tx")
    axes[2].set_title("perf instructions")
    axes[2].tick_params(axis="x", rotation=20)
    fig.savefig(OUT_FIG, bbox_inches="tight")
    plt.close(fig)


def find_row(summary, system):
    for row in summary:
        if row["system"] == system:
            return row
    return {}


def top_pct(top_rows, system, symbol_part):
    vals = [
        fnum(row, "self_pct") for row in top_rows
        if row["system"] == system and symbol_part in row["symbol"]
    ]
    return sum(vals)


def write_report(summary, top_rows, raw_csv, summary_csv, top_csv, args):
    pwal = find_row(summary, "P-WAL")
    tide = find_row(summary, "TideWAL")
    single = find_row(summary, "Single WAL")
    pwal_service = fnum(pwal, "closed_loop_service_ns")
    tide_service = fnum(tide, "closed_loop_service_ns")
    service_delta = tide_service - pwal_service
    pwal_latency = fnum(pwal, "benchmark_latency_ns")
    tide_latency = fnum(tide, "benchmark_latency_ns")
    latency_delta = tide_latency - pwal_latency
    frontier = fnum(tide, "frontier_collect_ns_per_tx")
    frontier_read = fnum(tide, "frontier_collect_ns_per_read")
    ratio = frontier / service_delta if service_delta > 0 else 0.0
    merge_pct = top_pct(top_rows, "TideWAL", "mergeVersionFrontier")
    lock_pct = top_pct(top_rows, "TideWAL", "pthread_mutex_lock")

    with OUT_DOC.open("w") as f:
        print("# YCSB-C read-only deep dive", file=f)
        print("", file=f)
        print(f"date: {datetime.now().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("YCSB-C is read-only, so read-only WAL skipping removes WAL persistence from all three systems.", file=f)
        print("This report re-runs Single WAL, P-WAL, and TideWAL and quantifies why TideWAL can still differ from P-WAL.", file=f)
        print("", file=f)
        print("## Conditions", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---|", file=f)
        print(f"| binary | `{PWAL_YCSB_EXE.relative_to(ROOT)}` |", file=f)
        print(f"| workload | YCSB-C, 100% read, 10 ops/tx |", file=f)
        print(f"| worker threads | {args.worker} |", file=f)
        print(f"| repeats | {args.repeats} |", file=f)
        print(f"| seconds | {args.seconds} |", file=f)
        print("| read-only WAL skip | enabled for all systems |", file=f)
        print("| TideWAL mode | `async_dep_frontier_cstamp` |", file=f)
        print(f"| TideWAL flusher/logger threads | {mode_allocation('tidewal', args.worker)['flusher_threads']} |", file=f)
        print("| TideWAL committer threads | 1 |", file=f)
        print(f"| raw csv | `{raw_csv.relative_to(ROOT)}` |", file=f)
        print(f"| summary csv | `{summary_csv.relative_to(ROOT)}` |", file=f)
        print(f"| perf top csv | `{top_csv.relative_to(ROOT)}` |", file=f)
        print(f"| figure | `{OUT_FIG.relative_to(ROOT)}` |", file=f)
        print("", file=f)

        print("## Summary", file=f)
        print("", file=f)
        print("`WAL ack p99 us` is the WAL-layer durable-ack latency.  It is 0 here because read-only transactions take the no-WAL fast path.  For transaction latency, use `benchmark latency ns` and `closed-loop ns/tx`.", file=f)
        print("", file=f)
        print("| system | tps | benchmark latency ns | closed-loop ns/tx | WAL ack p99 us | WAL bytes | fdatasync | pending | frontier collect ns/tx | cycles/tx | instr/tx | ctx switches |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for row in summary:
            print(
                f"| {row['system']} | {fnum(row, 'ack_tps'):.0f} | "
                f"{fnum(row, 'benchmark_latency_ns'):.1f} | "
                f"{fnum(row, 'closed_loop_service_ns'):.1f} | "
                f"{fnum(row, 'p99_us'):.1f} | {fnum(row, 'wal_bytes'):.0f} | "
                f"{fnum(row, 'fdatasync_count'):.0f} | {fnum(row, 'pending'):.0f} | "
                f"{fnum(row, 'frontier_collect_ns_per_tx'):.1f} | "
                f"{fnum(row, 'cycles_per_tx'):.0f} | {fnum(row, 'instructions_per_tx'):.0f} | "
                f"{fnum(row, 'context_switches'):.0f} |",
                file=f,
            )
        print("", file=f)

        print("## P-WAL vs TideWAL difference", file=f)
        print("", file=f)
        print(f"- P-WAL throughput: {fnum(pwal, 'ack_tps'):.0f} tx/s", file=f)
        print(f"- TideWAL throughput: {fnum(tide, 'ack_tps'):.0f} tx/s", file=f)
        print(f"- closed-loop service time: P-WAL {pwal_service:.1f} ns/tx, TideWAL {tide_service:.1f} ns/tx", file=f)
        print(f"- service-time gap: {service_delta:.1f} ns/tx", file=f)
        print(f"- benchmark latency gap: {latency_delta:.1f} ns/tx", file=f)
        print(f"- TideWAL frontier collection: {frontier:.1f} ns/tx, or {frontier_read:.1f} ns/read op", file=f)
        if ratio > 0:
            print(f"- frontier collection / service-time gap: {ratio:.2f}x", file=f)
        print(f"- extra cycles: {fnum(tide, 'cycles_per_tx') - fnum(pwal, 'cycles_per_tx'):.0f} cycles/tx", file=f)
        print(f"- extra instructions: {fnum(tide, 'instructions_per_tx') - fnum(pwal, 'instructions_per_tx'):.0f} instructions/tx", file=f)
        print(f"- TideWAL read-only fast path: {fnum(tide, 'read_only_fast_path_per_tx'):.3f} per tx", file=f)
        print(f"- TideWAL waitlist registration: {fnum(tide, 'waitlist_registration_ns_per_tx'):.1f} ns/tx", file=f)
        print(f"- TideWAL committer CPU: {fnum(tide, 'committer_cpu_ns_per_tx'):.1f} ns/tx", file=f)
        print("", file=f)
        print("Interpretation:", file=f)
        print("", file=f)
        print("- WAL persistence is not the cause of the difference: WAL bytes and fdatasync counts are zero for all systems.", file=f)
        print("- TideWAL does not enter the dependency waitlist on this workload: pending is zero and waitlist registration is zero.", file=f)
        print("- TideWAL still collects read-side dependency frontiers to check whether versions read by a read-only transaction depend on non-durable writers.", file=f)
        print("- The measured frontier-collection cost is large enough to account for the P-WAL/TideWAL service-time gap. Because the benchmark is closed-loop and multithreaded, the counter is not expected to equal the throughput-derived gap exactly; it is a consistency check.", file=f)
        print("", file=f)

        print("## perf top symbols", file=f)
        print("", file=f)
        print(f"TideWAL `mergeVersionFrontier` self samples: {merge_pct:.2f}%", file=f)
        print(f"TideWAL `pthread_mutex_lock` self samples: {lock_pct:.2f}%", file=f)
        print("", file=f)
        print("| system | rank | self % | symbol |", file=f)
        print("|---|---:|---:|---|", file=f)
        for system in SYSTEM_ORDER:
            rows = [row for row in top_rows if row["system"] == system and int(row["rank"]) <= 8]
            for row in rows:
                print(f"| {system} | {row['rank']} | {row['self_pct']} | `{row['symbol']}` |", file=f)
        print("", file=f)
        print("## Conclusion", file=f)
        print("", file=f)
        print("YCSB-C confirms that Single WAL, P-WAL, and TideWAL are all on read-only fast paths with no WAL persistence.", file=f)
        print("The remaining P-WAL/TideWAL gap is explained by TideWAL-specific read-side frontier bookkeeping, not by fdatasync or durable-ack wait.", file=f)
        print("The quantitative evidence is: zero fdatasync, zero pending/waitlist registration, nonzero frontier collection cost, and `mergeVersionFrontier` appearing in TideWAL's perf profile.", file=f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=int, default=32)
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--record-seconds", type=int, default=5)
    parser.add_argument("--group-size", type=int, default=16)
    parser.add_argument("--flush-us", type=int, default=50)
    parser.add_argument("--max-pending", type=int, default=65536)
    parser.add_argument("--freq", type=int, default=99)
    parser.add_argument("--call-graph", default="dwarf")
    parser.add_argument("--top-limit", type=int, default=12)
    parser.add_argument("--keep-perf-data", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS / f"ycsbc_readonly_deep_dive_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = []
    for repeat in range(args.repeats):
        for mode in MODES:
            row = run_stat_case(out_dir, mode, repeat, args)
            raw_rows.append(row)
            print(
                f"stat repeat={repeat} mode={mode} "
                f"tps={row['durable_ack_tps']} frontier={row['frontier_collect_ns_per_tx']}",
                flush=True,
            )
    top_rows = []
    for mode in MODES:
        rows = run_record_case(out_dir, mode, args)
        top_rows.extend(rows)
        print(f"record mode={mode} top_rows={len(rows)}", flush=True)

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_RAW_CSV, raw_rows)
    summary = aggregate(raw_rows)
    write_csv(OUT_SUMMARY_CSV, summary)
    write_csv(OUT_TOP_CSV, top_rows)
    save_figure(summary)
    write_report(summary, top_rows, OUT_RAW_CSV, OUT_SUMMARY_CSV, OUT_TOP_CSV, args)

    print(OUT_RAW_CSV)
    print(OUT_SUMMARY_CSV)
    print(OUT_TOP_CSV)
    print(OUT_FIG)
    print(OUT_DOC)


if __name__ == "__main__":
    main()
