#!/usr/bin/env python3
import argparse
import csv
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from run_ermia_cstamp_pwal_experiments import (
    PWAL_YCSB_EXE,
    RESULTS,
    ROOT,
    WORKLOAD_PRESETS,
    derived,
    write_csv,
)
from run_ycsbabc_ayame_worker_threads import (
    SYSTEM_LABELS,
    AYAME_LOGGERS,
    display_system,
    mode_allocation,
    parse_metrics,
    safe_float,
)


FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"
OUT_STAT_CSV = TABLE_DIR / "ayame_perf_stat_20260609.csv"
OUT_TOP_CSV = TABLE_DIR / "ayame_perf_top_20260609.csv"
OUT_DOC = ROOT / "docs" / "ayame_perf_eval_20260609.md"

MODES = ["single_wal", "pwal", "tidewal"]
SYSTEM_ORDER = ["Single WAL", "P-WAL", "Ayame"]
WORKLOAD_ORDER = ["YCSB-A", "YCSB-B", "YCSB-C"]
WORKLOAD_LABELS = {
    "ycsb_a": "YCSB-A",
    "ycsb_b": "YCSB-B",
    "ycsb_c": "YCSB-C",
}

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


def parse_workloads(text):
    workloads = [x for x in text.split(",") if x]
    unknown = [x for x in workloads if x not in WORKLOAD_PRESETS]
    if unknown:
        raise ValueError(f"unknown workload(s): {', '.join(unknown)}")
    return workloads


def run_env(mode, worker, args, out_dir, case_name):
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
        raise ValueError(f"unknown mode: {mode}")
    return env, alloc


def ycsb_cmd(workload, worker, seconds):
    preset = WORKLOAD_PRESETS[workload]
    return [
        "numactl", "--interleave=all",
        str(PWAL_YCSB_EXE),
        f"--thread_num={worker}",
        f"--extime={seconds}",
        "--clocks_per_us=1800",  # host Xeon Gold 5418N invariant TSC; binary default 2100 is wrong here
        "--ycsb_tuple_num=100000",
        f"--ycsb_max_ope={preset['ycsb_max_ope']}",
        f"--ycsb_rratio={preset['ycsb_rratio']}",
    ]


def parse_perf_stat(path):
    result = {}
    for row in csv.reader(path.open()):
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


def perf_row(workload, mode, repeat, worker, alloc, metrics, perf, seconds):
    row = {
        "workload_mode": workload,
        "workload": WORKLOAD_LABELS[workload],
        "mode": mode,
        "system": SYSTEM_LABELS[mode],
        "repeat": str(repeat),
        "worker_threads": str(worker),
        "wal_streams": str(alloc["wal_streams"]),
        "flusher_threads": str(alloc["flusher_threads"]),
        "committer_threads": str(alloc["committer_threads"]),
        "total_active_threads": str(
            worker + alloc["flusher_threads"] + alloc["committer_threads"]
            if mode == "tidewal" else worker
        ),
        "seconds": str(seconds),
    }
    row.update(metrics)
    row = derived(row)
    actual = safe_float(row.get("actual_extime")) or safe_float(row.get("actual_sec")) or seconds
    logical = safe_float(row.get("derived_logical_commits")) or 1.0
    acked = safe_float(row.get("derived_acked_commits")) or logical
    task_clock_ms = perf.get("task-clock", 0.0)
    cycles = perf.get("cycles", 0.0)
    instructions = perf.get("instructions", 0.0)
    cache_refs = perf.get("cache-references", 0.0)
    cache_misses = perf.get("cache-misses", 0.0)
    llc_loads = perf.get("LLC-loads", 0.0)
    llc_misses = perf.get("LLC-load-misses", 0.0)
    row.update({
        "perf_task_clock_ms": f"{task_clock_ms:.6f}",
        "perf_cpu_util_cores": f"{task_clock_ms / max(actual * 1000.0, 1.0):.6f}",
        "perf_context_switches": f"{perf.get('context-switches', 0.0):.0f}",
        "perf_cpu_migrations": f"{perf.get('cpu-migrations', 0.0):.0f}",
        "perf_page_faults": f"{perf.get('page-faults', 0.0):.0f}",
        "perf_cycles": f"{cycles:.0f}",
        "perf_instructions": f"{instructions:.0f}",
        "perf_cycles_per_tx": f"{cycles / max(logical, 1.0):.6f}",
        "perf_instructions_per_tx": f"{instructions / max(logical, 1.0):.6f}",
        "perf_ipc": f"{instructions / max(cycles, 1.0):.6f}",
        "perf_cache_miss_pct": f"{100.0 * cache_misses / max(cache_refs, 1.0):.6f}",
        "perf_llc_load_miss_pct": f"{100.0 * llc_misses / max(llc_loads, 1.0):.6f}",
        "durable_ack_tps": f"{acked / max(actual, 1.0):.6f}",
    })
    return row


def run_stat_case(out_dir, workload, mode, repeat, args):
    worker = args.worker
    case = f"{workload}_{mode}_r{repeat}"
    env, alloc = run_env(mode, worker, args, out_dir, case)
    logs = out_dir / "perf_stat_logs"
    logs.mkdir(parents=True, exist_ok=True)
    out_path = logs / f"{case}.out"
    err_path = logs / f"{case}.err"
    stat_path = logs / f"{case}.perfstat.csv"
    cmd = [
        "perf", "stat", "-x,", "-e", ",".join(EVENTS),
        "-o", str(stat_path), "--", *ycsb_cmd(workload, worker, args.seconds)
    ]
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)
    shutil.rmtree(out_dir / "wal" / case, ignore_errors=True)
    metrics = parse_metrics(out_path.read_text(errors="replace"))
    row = perf_row(workload, mode, repeat, worker, alloc, metrics,
                   parse_perf_stat(stat_path), args.seconds)
    row.update({
        "exit_code": str(proc.returncode),
        "stdout_log": str(out_path.relative_to(ROOT)),
        "stderr_log": str(err_path.relative_to(ROOT)),
        "perfstat_log": str(stat_path.relative_to(ROOT)),
    })
    return row


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


def run_record_case(out_dir, workload, mode, args):
    worker = args.worker
    case = f"{workload}_{mode}"
    env, alloc = run_env(mode, worker, args, out_dir, f"record_{case}")
    logs = out_dir / "perf_record_logs"
    logs.mkdir(parents=True, exist_ok=True)
    out_path = logs / f"{case}.out"
    err_path = logs / f"{case}.err"
    data_path = logs / f"{case}.perf.data"
    report_path = logs / f"{case}.report.nochildren.txt"
    report_err_path = logs / f"{case}.report.err"
    # Start sampling only after the parallel DB build (makeDB/partTableInit,
    # ~40 ms for 100k tuples) so the symbol profile reflects steady state.
    # Without this delay the multi-core init burst is over-sampled and shows up
    # as ~8% YcsbWorkload::partTableInit in the read-only profile.
    cmd = [
        "perf", "record", "-F", str(args.freq), "-g", "--call-graph", args.call_graph,
        "-D", str(args.record_delay_ms),
        "-o", str(data_path), "--", *ycsb_cmd(workload, worker, args.record_seconds)
    ]
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)
    shutil.rmtree(out_dir / "wal" / f"record_{case}", ignore_errors=True)
    if proc.returncode == 0:
        with report_path.open("w") as out, report_err_path.open("w") as err:
            subprocess.run([
                "perf", "report", "-i", str(data_path), "--stdio", "--no-children",
                "--sort", "symbol,dso", "--percent-limit", "0.5"
            ], cwd=ROOT, stdout=out, stderr=err)
    top_rows = []
    if report_path.exists():
        for rank, row in enumerate(parse_top_symbols(report_path, args.top_limit), start=1):
            row.update({
                "workload_mode": workload,
                "workload": WORKLOAD_LABELS[workload],
                "mode": mode,
                "system": SYSTEM_LABELS[mode],
                "rank": str(rank),
                "worker_threads": str(worker),
                "report_log": str(report_path.relative_to(ROOT)),
            })
            top_rows.append(row)
    if not args.keep_perf_data and data_path.exists():
        data_path.unlink()
    return top_rows


def aggregate_stat(rows):
    groups = {}
    for row in rows:
        key = (row["workload"], display_system(row))
        groups.setdefault(key, []).append(row)
    out = []
    for (workload, system), rs in sorted(groups.items()):
        def avg(key):
            return sum(safe_float(r.get(key)) for r in rs) / len(rs)
        def med(key):
            vals = sorted(safe_float(r.get(key)) for r in rs)
            n = len(vals)
            return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
        def p99_us():
            value = med("ack_latency_p99_us")
            if value > 0:
                return value
            fallback = []
            for row in rs:
                tps = safe_float(row.get("durable_ack_tps"))
                workers = safe_float(row.get("worker_threads"))
                if tps > 0:
                    fallback.append(workers * 1_000_000.0 / tps)
            fallback.sort()
            if not fallback:
                return 0.0
            n = len(fallback)
            return fallback[n // 2] if n % 2 else (fallback[n // 2 - 1] + fallback[n // 2]) / 2.0
        fdatasync_count = avg("wal_stats_fdatasync_count")
        out.append({
            "workload": workload,
            "system": system,
            "worker_threads": rs[0]["worker_threads"],
            "wal_streams": rs[0]["wal_streams"],
            "flusher_threads": rs[0]["flusher_threads"],
            "committer_threads": rs[0]["committer_threads"],
            "total_active_threads": rs[0]["total_active_threads"],
            "durable_ack_tps": avg("durable_ack_tps"),
            "cpu_util_cores": avg("perf_cpu_util_cores"),
            "cycles_per_tx": avg("perf_cycles_per_tx"),
            "instructions_per_tx": avg("perf_instructions_per_tx"),
            "ipc": avg("perf_ipc"),
            "context_switches": avg("perf_context_switches"),
            "cache_miss_pct": avg("perf_cache_miss_pct"),
            "llc_load_miss_pct": avg("perf_llc_load_miss_pct"),
            "fdatasync_count": fdatasync_count,
            "commits_per_fdatasync": (
                avg("commits_per_fdatasync") if fdatasync_count > 0 else 0.0
            ),
            "pending": avg("pending_commits"),
            "p99_us": p99_us(),
            "frontier_collect_ns_per_tx": avg("frontier_collect_ns_per_tx"),
            "frontier_publish_ns_per_tx": avg("frontier_publish_ns_per_tx"),
            "waitlist_registration_ns_per_tx": avg("waitlist_registration_ns_per_tx"),
        })
    return out


def write_report(summary, top_rows, raw_csv, top_csv, args):
    with OUT_DOC.open("w") as f:
        print("# Ayame perf evaluation", file=f)
        print("", file=f)
        print(f"date: {datetime.now().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("This perf run uses only the three paper systems: Single WAL, P-WAL, and Ayame.", file=f)
        print("It is internal analysis, not a component ablation.", file=f)
        print("", file=f)
        print("## Conditions", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---|", file=f)
        print(f"| workloads | {args.workloads} |", file=f)
        print(f"| worker threads | {args.worker} |", file=f)
        print(f"| perf stat repeats | {args.repeats} |", file=f)
        print(f"| perf stat seconds | {args.seconds} |", file=f)
        print(f"| perf record seconds | {args.record_seconds} |", file=f)
        print(f"| events | {','.join(EVENTS)} |", file=f)
        print(f"| stat csv | `{raw_csv.relative_to(ROOT)}` |", file=f)
        print(f"| top csv | `{top_csv.relative_to(ROOT)}` |", file=f)
        print("", file=f)
        print("## perf stat summary", file=f)
        print("", file=f)
        print("| workload | system | ack tps | CPU cores | cycles/tx | instr/tx | IPC | ctx switches | fdatasync | commits/fdatasync | pending | p99 us |", file=f)
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for workload in WORKLOAD_ORDER:
            for system in SYSTEM_ORDER:
                match = [r for r in summary if r["workload"] == workload and r["system"] == system]
                if not match:
                    continue
                r = match[0]
                print(
                    f"| {workload} | {system} | {r['durable_ack_tps']:.0f} | "
                    f"{r['cpu_util_cores']:.2f} | {r['cycles_per_tx']:.0f} | "
                    f"{r['instructions_per_tx']:.0f} | {r['ipc']:.3f} | "
                    f"{r['context_switches']:.0f} | {r['fdatasync_count']:.0f} | "
                    f"{r['commits_per_fdatasync']:.2f} | {r['pending']:.0f} | "
                    f"{r['p99_us']:.0f} |",
                    file=f,
                )
        print("", file=f)
        print("## perf top symbols", file=f)
        print("", file=f)
        for workload in WORKLOAD_ORDER:
            print(f"### {workload}", file=f)
            print("", file=f)
            print("| system | rank | self % | symbol |", file=f)
            print("|---|---:|---:|---|", file=f)
            for system in SYSTEM_ORDER:
                rows = [
                    r for r in top_rows
                    if r["workload"] == workload and r["system"] == system and int(r["rank"]) <= 5
                ]
                for r in rows:
                    print(f"| {system} | {r['rank']} | {r['self_pct']} | `{r['symbol']}` |", file=f)
            print("", file=f)


def read_csv_rows(path):
    with Path(path).open() as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["system"] = display_system(row)
    return rows


def workspace_path(path):
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workloads", default="ycsb_a,ycsb_b,ycsb_c")
    parser.add_argument("--worker", type=int, default=48)
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--record-seconds", type=int, default=5)
    parser.add_argument("--group-size", type=int, default=64)
    parser.add_argument("--flush-us", type=int, default=50)
    parser.add_argument("--max-pending", type=int, default=65536)
    parser.add_argument("--freq", type=int, default=99)
    parser.add_argument("--call-graph", default="dwarf")
    parser.add_argument("--record-delay-ms", dest="record_delay_ms", type=int, default=500,
                        help="Delay perf record start by this many ms to skip the DB "
                             "build phase and profile only steady state.")
    parser.add_argument("--top-limit", type=int, default=12)
    parser.add_argument("--skip-record", action="store_true")
    parser.add_argument("--keep-perf-data", action="store_true")
    parser.add_argument("--input-csv")
    parser.add_argument("--input-top-csv")
    args = parser.parse_args()

    workloads = parse_workloads(args.workloads)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS / f"ayame_perf_eval_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.input_csv:
        raw_csv = workspace_path(args.input_csv)
        stat_rows = read_csv_rows(raw_csv)
    else:
        stat_rows = []
        for repeat in range(args.repeats):
            for workload in workloads:
                for mode in MODES:
                    row = run_stat_case(out_dir, workload, mode, repeat, args)
                    stat_rows.append(row)
                    print(
                        f"perf-stat repeat={repeat} workload={workload} mode={mode} "
                        f"ack_tps={row['durable_ack_tps']} cpu={row['perf_cpu_util_cores']}",
                        flush=True,
                    )

        raw_csv = out_dir / f"ayame_perf_stat_raw_{stamp}.csv"
        write_csv(raw_csv, stat_rows)
    summary = aggregate_stat(stat_rows)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_STAT_CSV, summary)

    if args.input_top_csv:
        top_rows = read_csv_rows(workspace_path(args.input_top_csv))
    else:
        top_rows = []
    if not args.input_top_csv and not args.skip_record:
        for workload in workloads:
            for mode in MODES:
                rows = run_record_case(out_dir, workload, mode, args)
                top_rows.extend(rows)
                print(f"perf-record workload={workload} mode={mode} top_rows={len(rows)}",
                      flush=True)
    write_csv(OUT_TOP_CSV, top_rows)
    write_report(summary, top_rows, raw_csv, OUT_TOP_CSV, args)

    print(raw_csv)
    print(OUT_STAT_CSV)
    print(OUT_TOP_CSV)
    print(OUT_DOC)


if __name__ == "__main__":
    main()
