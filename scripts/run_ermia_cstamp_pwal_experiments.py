#!/usr/bin/env python3
import argparse
import csv
import os
import re
import subprocess
import statistics
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

NO_DUR_EXE = ROOT / "build" / "cc" / "ermia" / "ycsb_abort0_ermia.exe"
NO_DUR_YCSB_EXE = ROOT / "build" / "cc" / "ermia" / "ycsb_ermia.exe"
PWAL_ABORT0_EXE = ROOT / "build" / "cc" / "ermia_pwal" / "ycsb_abort0_ermia_pwal.exe"
PWAL_YCSB_EXE = ROOT / "build" / "cc" / "ermia_pwal" / "ycsb_ermia_pwal.exe"
CORRECTNESS_EXE = ROOT / "build" / "ermia_cstamp_pwal_correctness_test.exe"

COLORS = {
    "ermia_no_durability": "#444444",
    "ermia_pwal_group_global_prefix": "#d62728",
    "ermia_async_global_lsn_prefix": "#1f77b4",
    "ermia_async_dep_frontier_lsn": "#17becf",
    "ermia_async_dep_frontier_cstamp": "#2ca02c",
    "ermia_async_dep_frontier_cstamp_no_publish": "#ff7f0e",
    "ermia_async_dep_frontier_cstamp_zero_dep": "#7f7f7f",
    "ermia_async_dep_frontier_cstamp_prealloc": "#bcbd22",
    "ermia_async_dep_frontier_lsn_real_io": "#17becf",
    "ermia_async_dep_frontier_cstamp_real_io": "#2ca02c",
    "ermia_async_dep_frontier_lsn_io_light": "#9467bd",
    "ermia_async_dep_frontier_cstamp_io_light": "#8c564b",
}

MODE_ORDER = list(COLORS.keys())

MODE_ENV = {
    "ermia_pwal_group_global_prefix": "group_global_prefix",
    "ermia_async_global_lsn_prefix": "async_global_lsn_prefix",
    "ermia_async_dep_frontier_lsn": "async_dep_frontier_lsn",
    "ermia_async_dep_frontier_cstamp": "async_dep_frontier_cstamp",
    "ermia_async_dep_frontier_cstamp_no_publish": "async_dep_frontier_cstamp_no_publish",
    "ermia_async_dep_frontier_cstamp_zero_dep": "async_dep_frontier_cstamp_zero_dep",
    "ermia_async_dep_frontier_cstamp_prealloc": "async_dep_frontier_cstamp_prealloc",
}

TOTAL_BUDGET_ASYNC_MODES = {
    "ermia_pwal_group_global_prefix",
    "ermia_async_global_lsn_prefix",
    "ermia_async_dep_frontier_lsn",
    "ermia_async_dep_frontier_cstamp",
}

TOTAL_BUDGET_MODES = [
    "ermia_no_durability",
    "ermia_pwal_group_global_prefix",
    "ermia_async_global_lsn_prefix",
    "ermia_async_dep_frontier_cstamp",
]

TOTAL_BUDGET_DEFAULT_ALLOC = {
    4: (2, 1, 1),
    8: (5, 2, 1),
    16: (12, 3, 1),
    24: (18, 5, 1),
    32: (24, 7, 1),
    48: (38, 9, 1),
    96: (76, 19, 1),
}

WORKLOAD_PRESETS = {
    "abort0_write1": {
        "binary": "abort0",
        "ycsb_max_ope": 1,
        "ycsb_rratio": 0,
        "remote_ppm": 0,
        "description": "abort0 / partitioned / 1 write op per transaction",
    },
    "ycsb_write1": {
        "binary": "ycsb",
        "ycsb_max_ope": 1,
        "ycsb_rratio": 0,
        "remote_ppm": 0,
        "description": "normal YCSB / 1 write op per transaction",
    },
    "ycsb_a": {
        "binary": "ycsb",
        "ycsb_max_ope": 10,
        "ycsb_rratio": 50,
        "remote_ppm": 0,
        "description": "normal YCSB-A style / 50% read, 50% update, 10 ops/tx",
    },
    "ycsb_b": {
        "binary": "ycsb",
        "ycsb_max_ope": 10,
        "ycsb_rratio": 95,
        "remote_ppm": 0,
        "description": "normal YCSB-B style / 95% read, 5% update, 10 ops/tx",
    },
    "ycsb_c": {
        "binary": "ycsb",
        "ycsb_max_ope": 10,
        "ycsb_rratio": 100,
        "remote_ppm": 0,
        "description": "normal YCSB-C style / 100% read, 10 ops/tx",
    },
}


def parse_list(text, cast=int):
    return [cast(x) for x in text.split(",") if x != ""]


def parse_workloads(text):
    workloads = [x for x in text.split(",") if x != ""]
    unknown = [x for x in workloads if x not in WORKLOAD_PRESETS]
    if unknown:
        raise ValueError(f"unknown workload preset(s): {', '.join(unknown)}")
    return workloads


def read_cpu_topology():
    try:
        proc = subprocess.run(
            ["lscpu", "-p=CPU,CORE,SOCKET,NODE,ONLINE"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [
            {"cpu": cpu, "core": cpu, "socket": 0, "node": 0}
            for cpu in range(os.cpu_count() or 1)
        ]
    entries = []
    for line in proc.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 5 or parts[4].strip().upper() != "Y":
            continue
        entries.append(
            {
                "cpu": int(parts[0]),
                "core": int(parts[1]),
                "socket": int(parts[2]),
                "node": int(parts[3]),
            }
        )
    return sorted(entries, key=lambda e: e["cpu"])


def physical_cpu_entries(entries):
    first = {}
    for e in sorted(entries, key=lambda row: row["cpu"]):
        first.setdefault((e["socket"], e["core"]), e)
    return sorted(first.values(), key=lambda row: row["cpu"])


def cpu_list_for_total(total_threads):
    entries = read_cpu_topology()
    physical = physical_cpu_entries(entries)
    sockets = sorted({e["socket"] for e in physical})
    per_socket = {
        socket: [e for e in physical if e["socket"] == socket]
        for socket in sockets
    }
    max_single_socket = max((len(v) for v in per_socket.values()), default=0)
    if total_threads <= max_single_socket:
        socket = max(per_socket, key=lambda s: (len(per_socket[s]), -s))
        cpus = [e["cpu"] for e in per_socket[socket][:total_threads]]
        policy = f"socket{socket}_physical"
    elif total_threads <= len(physical):
        cpus = [e["cpu"] for e in physical[:total_threads]]
        policy = "all_socket_physical"
    else:
        cpus = [e["cpu"] for e in entries[:total_threads]]
        policy = "all_logical"
    if len(cpus) < total_threads:
        raise RuntimeError(
            f"requested {total_threads} total threads but only {len(cpus)} CPUs are available"
        )
    return cpus, policy


def total_budget_allocation(total_threads, mode):
    if mode == "ermia_no_durability":
        return total_threads, 0, 0
    if total_threads < 4:
        return None
    if total_threads in TOTAL_BUDGET_DEFAULT_ALLOC:
        return TOTAL_BUDGET_DEFAULT_ALLOC[total_threads]
    committer = 1
    flusher = max(1, round((total_threads - committer) / 5))
    worker = total_threads - flusher - committer
    if worker < 1:
        return None
    return worker, flusher, committer


def cpu_list_text(cpus):
    return ",".join(str(cpu) for cpu in cpus)


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        m = re.match(r"^#?([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            row[m.group(1)] = m.group(2).strip()
    return row


def fnum(row, key):
    try:
        return float(row.get(key, "0") or 0)
    except ValueError:
        return 0.0


def mean(rows, key):
    vals = [fnum(r, key) for r in rows]
    return statistics.mean(vals) if vals else 0.0


def stdev(rows, key):
    vals = [fnum(r, key) for r in rows]
    return statistics.stdev(vals) if len(vals) > 1 else 0.0


def build():
    subprocess.run(
        [
            "cmake",
            "--build",
            "build",
            "--target",
            "ycsb_ermia.exe",
            "ycsb_abort0_ermia.exe",
            "ycsb_abort0_ermia_pwal.exe",
            "ycsb_ermia_pwal.exe",
            "ermia_cstamp_pwal_correctness_test.exe",
            "-j2",
        ],
        cwd=ROOT,
        check=True,
    )


def derived(row):
    actual = fnum(row, "actual_extime") or fnum(row, "actual_sec") or 1.0
    logical = fnum(row, "wal_stats_measured_logical_commits")
    acked = fnum(row, "wal_stats_measured_acked_commits")
    if logical == 0:
        logical = fnum(row, "commit_counts_")
    if acked == 0:
        acked = logical
    row["derived_logical_commits"] = f"{logical:.0f}"
    row["derived_acked_commits"] = f"{acked:.0f}"
    row["durable_ack_tps"] = f"{acked / actual:.6f}"
    row["logical_tps"] = f"{logical / actual:.6f}"
    row["fdatasync_per_sec"] = f"{fnum(row, 'wal_stats_fdatasync_count') / actual:.6f}"
    row["bytes_flushed_per_sec"] = f"{fnum(row, 'wal_stats_bytes') / actual:.6f}"
    denom = max(logical, 1.0)
    row["global_atomic_per_tx"] = f"{fnum(row, 'wal_stats_global_atomic_count') / denom:.6f}"
    row["lsn_alloc_ns_per_tx"] = f"{fnum(row, 'wal_stats_lsn_alloc_ns') / denom:.6f}"
    row["payload_build_ns_per_tx"] = f"{fnum(row, 'wal_stats_payload_build_ns') / denom:.6f}"
    row["wal_enqueue_ns_per_tx"] = f"{fnum(row, 'wal_stats_wal_enqueue_ns') / denom:.6f}"
    row["version_install_ns_per_tx"] = f"{fnum(row, 'wal_stats_version_install_ns') / denom:.6f}"
    row["frontier_collect_ns_per_tx"] = f"{fnum(row, 'wal_stats_frontier_collect_ns') / denom:.6f}"
    row["frontier_merge_ns_per_tx"] = f"{fnum(row, 'wal_stats_frontier_merge_ns') / denom:.6f}"
    row["frontier_publish_ns_per_tx"] = f"{fnum(row, 'wal_stats_frontier_publish_ns') / denom:.6f}"
    row["frontier_bytes_per_tx"] = f"{fnum(row, 'wal_stats_dep_frontier_bytes') / denom:.6f}"
    row["frontier_entries_per_tx"] = f"{fnum(row, 'wal_stats_dep_frontier_entries') / denom:.6f}"
    row["frontier_nonzero_entries_per_tx"] = f"{fnum(row, 'wal_stats_dep_frontier_nonzero_entries') / denom:.6f}"
    row["read_frontier_updates_per_tx"] = f"{fnum(row, 'wal_stats_read_frontier_updates') / denom:.6f}"
    row["write_frontier_updates_per_tx"] = f"{fnum(row, 'wal_stats_write_frontier_updates') / denom:.6f}"
    row["frontier_alloc_count_per_tx"] = f"{fnum(row, 'wal_stats_frontier_alloc_count') / denom:.6f}"
    row["frontier_shared_ptr_count_per_tx"] = f"{fnum(row, 'wal_stats_frontier_shared_ptr_count') / denom:.6f}"
    row["dep_wait_conditions_per_tx"] = f"{fnum(row, 'wal_stats_dep_wait_conditions') / denom:.6f}"
    row["waitlist_registrations_per_tx"] = f"{fnum(row, 'wal_stats_waitlist_registrations') / denom:.6f}"
    row["waitlist_pops_per_tx"] = f"{fnum(row, 'wal_stats_waitlist_pops') / denom:.6f}"
    row["waiting_conditions_per_tx"] = row["dep_wait_conditions_per_tx"]
    row["waitlist_registration_ns_per_tx"] = f"{fnum(row, 'wal_stats_waitlist_registration_ns') / denom:.6f}"
    row["committer_event_ns_per_tx"] = f"{fnum(row, 'wal_stats_committer_event_ns') / denom:.6f}"
    row["ready_queue_push_ns_per_tx"] = f"{fnum(row, 'wal_stats_ready_queue_push_ns') / denom:.6f}"
    row["ack_process_ns_per_tx"] = f"{fnum(row, 'wal_stats_ack_process_ns') / denom:.6f}"
    row["read_only_frontier_collect_skipped_per_tx"] = f"{fnum(row, 'wal_stats_read_only_frontier_collect_skipped') / denom:.6f}"
    row["flusher_cpu_ns_per_tx"] = f"{fnum(row, 'wal_stats_flusher_cpu_ns') / denom:.6f}"
    row["committer_cpu_ns_per_tx"] = f"{fnum(row, 'wal_stats_committer_cpu_ns') / denom:.6f}"
    row["write_ns_per_tx"] = f"{fnum(row, 'wal_stats_write_ns') / denom:.6f}"
    row["fdatasync_ns_per_tx"] = f"{fnum(row, 'wal_stats_fdatasync_ns') / denom:.6f}"
    row["worker_stall_ns_per_tx"] = f"{fnum(row, 'wal_stats_worker_stall_ns') / denom:.6f}"
    row["commits_per_fdatasync"] = f"{logical / max(fnum(row, 'wal_stats_fdatasync_count'), 1.0):.6f}"
    row["avg_batch_size"] = row.get("wal_stats_avg_batch_size", "0")
    row["max_waitlist_len"] = row.get("wal_stats_max_waitlist_len", "0")
    row["avg_waitlist_len"] = row.get("wal_stats_avg_waitlist_len", "0")
    row["max_ready_queue_len"] = row.get("wal_stats_max_ready_queue_len", "0")
    row["avg_ready_queue_len"] = row.get("wal_stats_avg_ready_queue_len", "0")
    ack_denom = max(acked, 1.0)
    row["queue_wait_us_per_acked_tx"] = f"{fnum(row, 'wal_stats_committer_queue_wait_ns') / ack_denom / 1000.0:.6f}"
    row["ack_latency_p50_us"] = row.get(
        "wal_stats_measured_ack_latency_p50_us",
        row.get("wal_stats_ack_latency_p50_us", "0"),
    )
    row["ack_latency_p90_us"] = row.get(
        "wal_stats_measured_ack_latency_p90_us",
        row.get("wal_stats_ack_latency_p90_us", "0"),
    )
    row["ack_latency_p95_us"] = row.get(
        "wal_stats_measured_ack_latency_p95_us",
        row.get("wal_stats_ack_latency_p95_us", "0"),
    )
    row["ack_latency_p99_us"] = row.get(
        "wal_stats_measured_ack_latency_p99_us",
        row.get("wal_stats_ack_latency_p99_us", "0"),
    )
    row["ack_latency_p999_us"] = row.get(
        "wal_stats_measured_ack_latency_p999_us",
        row.get("wal_stats_ack_latency_p999_us", "0"),
    )
    row["ack_latency_max_us"] = row.get(
        "wal_stats_measured_ack_latency_max_us",
        row.get("wal_stats_ack_latency_max_us", "0"),
    )
    row["pending_commits"] = row.get("wal_stats_measurement_pending_commits", "0")
    row["logical_minus_acked"] = row.get("wal_stats_measured_logical_minus_acked", "0")
    return row


def run_case(out_dir, mode, repeat, thread_num, seconds, ycsb_max_ope, ycsb_rratio,
             logger_num, group_size, flush_us, max_pending, remote_ppm=0,
             straggler_logger=-1, straggler_sleep_us=0, skip_fdatasync=0,
             workload_kind="abort0", workload_mode="custom", committer_num=1,
             total_threads=None, cpu_list=None, cpu_policy="", use_numactl=False):
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir = out_dir / "wal"

    env = os.environ.copy()
    env["CCBENCH_WAL_DIR"] = str(wal_dir)
    env["CCBENCH_WAL_LOGGER_NUM"] = str(logger_num)
    env["CCBENCH_WAL_COMMITTER_NUM"] = str(committer_num)
    env["CCBENCH_WAL_GROUP_SIZE"] = str(group_size)
    env["CCBENCH_WAL_FLUSH_US"] = str(flush_us)
    env["CCBENCH_WAL_MAX_PENDING"] = str(max_pending)
    env["CCBENCH_WAL_STRAGGLER_LOGGER"] = str(straggler_logger)
    env["CCBENCH_WAL_STRAGGLER_SLEEP_US"] = str(straggler_sleep_us)
    env["CCBENCH_WAL_SKIP_FDATASYNC"] = str(skip_fdatasync)
    if cpu_list:
        env["CCBENCH_CPU_LIST"] = cpu_list_text(cpu_list)

    if mode == "ermia_no_durability":
        exe = NO_DUR_EXE if workload_kind == "abort0" else NO_DUR_YCSB_EXE
    else:
        exe = PWAL_ABORT0_EXE if workload_kind == "abort0" else PWAL_YCSB_EXE
        env["CCBENCH_WAL_DURABLE_MODE"] = MODE_ENV[mode]

    cmd = [
        str(exe),
        f"--thread_num={thread_num}",
        f"--extime={seconds}",
        "--ycsb_tuple_num=100000",
        f"--ycsb_max_ope={ycsb_max_ope}",
        f"--ycsb_rratio={ycsb_rratio}",
    ]
    if workload_kind == "abort0":
        cmd.append(f"--ycsb_remote_read_prob_ppm={remote_ppm}")
    if use_numactl:
        cmd = ["numactl", "--interleave=all", "--"] + cmd
    label = f"{workload_mode}_{mode}_th{thread_num}_remote{remote_ppm}_r{repeat}"
    label += f"_log{logger_num}_com{committer_num}_pend{max_pending}"
    if straggler_logger >= 0:
        label += f"_str{straggler_logger}_{straggler_sleep_us}"
    if skip_fdatasync:
        label += "_skipfsync"
    stdout_path = logs / f"{label}.out"
    stderr_path = logs / f"{label}.err"
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=out, stderr=err)

    row = parse_metrics(stdout_path.read_text(errors="replace"))
    row.update(
        {
            "mode": mode,
            "workload_mode": workload_mode,
            "workload_kind": workload_kind,
            "repeat": str(repeat),
            "thread_num": str(thread_num),
            "worker_threads": str(thread_num),
            "logger_threads": str(logger_num if mode != "ermia_no_durability" else 0),
            "committer_threads": str(committer_num if mode != "ermia_no_durability" else 0),
            "background_threads": str(
                (logger_num + committer_num) if mode != "ermia_no_durability" else 0
            ),
            "total_threads": str(total_threads if total_threads is not None else thread_num),
            "cpu_list": cpu_list_text(cpu_list) if cpu_list else "",
            "cpu_policy": cpu_policy,
            "numactl_interleave": str(1 if use_numactl else 0),
            "seconds": str(seconds),
            "ycsb_max_ope": str(ycsb_max_ope),
            "ycsb_rratio": str(ycsb_rratio),
            "logger_num": str(logger_num),
            "committer_num": str(committer_num),
            "group_size": str(group_size),
            "flush_us": str(flush_us),
            "max_pending": str(max_pending),
            "remote_read_prob_ppm": str(remote_ppm),
            "remote_read_prob": f"{remote_ppm / 1_000_000:.6f}",
            "straggler_logger": str(straggler_logger),
            "straggler_sleep_us": str(straggler_sleep_us),
            "skip_fdatasync": str(skip_fdatasync),
            "exit_code": str(proc.returncode),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }
    )
    return derived(row)


def write_svg(path, grouped, modes, xs, x_key, metric, title, y_label):
    width = 980
    height = 540
    left = 86
    right = 36
    top = 50
    bottom = 78
    plot_w = width - left - right
    plot_h = height - top - bottom
    ymax = 0.0
    xmin = min(xs)
    xmax = max(xs)
    if xmax == xmin:
        xmax = xmin + 1
    for mode in modes:
        for x in xs:
            ymax = max(ymax, mean(grouped.get((mode, x), []), metric))
    ymax = ymax * 1.10 if ymax else 1.0

    def x_pos(x):
        return left + ((x - xmin) / (xmax - xmin)) * plot_w

    def y_pos(y):
        return top + plot_h - (y / ymax) * plot_h

    with path.open("w") as f:
        print('<?xml version="1.0" encoding="UTF-8"?>', file=f)
        print(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', file=f)
        print('<rect width="100%" height="100%" fill="white"/>', file=f)
        print(f'<text x="{left}" y="30" font-family="sans-serif" font-size="20" font-weight="700">{title}</text>', file=f)
        print(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#333"/>', file=f)
        print(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#333"/>', file=f)
        for i in range(6):
            y = ymax * i / 5
            py = y_pos(y)
            print(f'<line x1="{left}" y1="{py:.1f}" x2="{left + plot_w}" y2="{py:.1f}" stroke="#e7e7e7"/>', file=f)
            print(f'<text x="{left - 10}" y="{py + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="12">{y:.0f}</text>', file=f)
        for x in xs:
            px = x_pos(x)
            print(f'<line x1="{px:.1f}" y1="{top + plot_h}" x2="{px:.1f}" y2="{top + plot_h + 5}" stroke="#333"/>', file=f)
            label = f"{x:.2f}" if x_key == "remote_read_prob" else str(int(x))
            print(f'<text x="{px:.1f}" y="{top + plot_h + 24}" text-anchor="middle" font-family="sans-serif" font-size="12">{label}</text>', file=f)
        print(f'<text x="{left + plot_w / 2:.1f}" y="{height - 22}" text-anchor="middle" font-family="sans-serif" font-size="14">{x_key}</text>', file=f)
        print(f'<text x="18" y="{top + plot_h / 2:.1f}" text-anchor="middle" font-family="sans-serif" font-size="14" transform="rotate(-90 18 {top + plot_h / 2:.1f})">{y_label}</text>', file=f)
        for mode in modes:
            color = COLORS.get(mode, "#555")
            pts = []
            for x in xs:
                pts.append(f"{x_pos(x):.1f},{y_pos(mean(grouped.get((mode, x), []), metric)):.1f}")
            print(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="3"/>', file=f)
            for x in xs:
                print(f'<circle cx="{x_pos(x):.1f}" cy="{y_pos(mean(grouped.get((mode, x), []), metric)):.1f}" r="4" fill="{color}"/>', file=f)
        lx = left + 12
        ly = top + 16
        for i, mode in enumerate(modes):
            y = ly + i * 22
            print(f'<rect x="{lx}" y="{y - 10}" width="14" height="14" fill="{COLORS.get(mode, "#555")}"/>', file=f)
            print(f'<text x="{lx + 22}" y="{y + 2}" font-family="sans-serif" font-size="13">{mode}</text>', file=f)
        print("</svg>", file=f)


def write_csv(path, rows):
    keys = sorted({k for r in rows for k in r.keys()})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def write_report(path, rows, modes, x_values, x_key, title):
    grouped = {}
    for r in rows:
        x = float(r[x_key]) if x_key == "remote_read_prob" else int(r[x_key])
        grouped.setdefault((r["mode"], x), []).append(r)
    throughput_svg = path.with_name(path.stem + "_throughput.svg")
    p99_svg = path.with_name(path.stem + "_p99.svg")
    atomic_svg = path.with_name(path.stem + "_atomic.svg")
    write_svg(throughput_svg, grouped, modes, x_values, x_key, "durable_ack_tps",
              title + " throughput", "durable ack tx/s")
    write_svg(p99_svg, grouped, modes, x_values, x_key, "ack_latency_p99_us",
              title + " p99 ack latency", "p99 us")
    write_svg(atomic_svg, grouped, modes, x_values, x_key, "global_atomic_per_tx",
              title + " global atomic cost", "atomic ops / tx")

    with path.open("w") as f:
        print(f"# {title}", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print(f"![throughput]({throughput_svg.name})", file=f)
        print("", file=f)
        print(f"![p99]({p99_svg.name})", file=f)
        print("", file=f)
        print(f"![atomic]({atomic_svg.name})", file=f)
        print("", file=f)
        print("## Summary", file=f)
        print("", file=f)
        print("| mode | x | ack tps mean | ack tps stdev | p99 us | pending | fdatasync/s | atomic/tx | frontier bytes/tx | lsn alloc ns/tx |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for x in x_values:
            for mode in modes:
                rs = grouped.get((mode, x), [])
                if not rs:
                    continue
                print(
                    "| "
                    + " | ".join(
                        [
                            mode,
                            f"{x:.6f}" if x_key == "remote_read_prob" else str(x),
                            f"{mean(rs, 'durable_ack_tps'):.0f}",
                            f"{stdev(rs, 'durable_ack_tps'):.1f}",
                            f"{mean(rs, 'ack_latency_p99_us'):.0f}",
                            f"{mean(rs, 'pending_commits'):.0f}",
                            f"{mean(rs, 'fdatasync_per_sec'):.1f}",
                            f"{mean(rs, 'global_atomic_per_tx'):.3f}",
                            f"{mean(rs, 'frontier_bytes_per_tx'):.1f}",
                            f"{mean(rs, 'lsn_alloc_ns_per_tx'):.1f}",
                        ]
                    )
                    + " |",
                    file=f,
                )


def write_workload_report(path, rows, modes, thread_values, workloads, title):
    workload_svgs = []
    for workload in workloads:
        workload_rows = [r for r in rows if r.get("workload_mode") == workload]
        grouped = {}
        for r in workload_rows:
            grouped.setdefault((r["mode"], int(r["thread_num"])), []).append(r)
        throughput_svg = path.with_name(path.stem + f"_{workload}_throughput.svg")
        p99_svg = path.with_name(path.stem + f"_{workload}_p99.svg")
        write_svg(throughput_svg, grouped, modes, thread_values, "thread_num",
                  "durable_ack_tps", "throughput: " + workload,
                  "durable ack tx/s")
        write_svg(p99_svg, grouped, modes, thread_values, "thread_num",
                  "ack_latency_p99_us", "p99 ack latency: " + workload,
                  "p99 us")
        workload_svgs.append((workload, throughput_svg, p99_svg))

    grouped_all = {}
    for r in rows:
        grouped_all.setdefault(
            (r.get("workload_mode", "custom"), r["mode"], int(r["thread_num"])),
            [],
        ).append(r)

    with path.open("w") as f:
        print(f"# {title}", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("## Workloads", file=f)
        print("", file=f)
        print("| workload | meaning |", file=f)
        print("|---|---|", file=f)
        for workload in workloads:
            print(f"| {workload} | {WORKLOAD_PRESETS[workload]['description']} |", file=f)
        print("", file=f)
        for workload, throughput_svg, p99_svg in workload_svgs:
            print(f"## {workload}", file=f)
            print("", file=f)
            print(f"![throughput]({throughput_svg.name})", file=f)
            print("", file=f)
            print(f"![p99]({p99_svg.name})", file=f)
            print("", file=f)
            print("| mode | thread | ack tps mean | ack tps stdev | p99 us | abort rate | pending | fdatasync/s | atomic/tx | frontier bytes/tx | lsn alloc ns/tx |", file=f)
            print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|", file=f)
            for thread in thread_values:
                for mode in modes:
                    rs = grouped_all.get((workload, mode, thread), [])
                    if not rs:
                        continue
                    print(
                        "| "
                        + " | ".join(
                            [
                                mode,
                                str(thread),
                                f"{mean(rs, 'durable_ack_tps'):.0f}",
                                f"{stdev(rs, 'durable_ack_tps'):.1f}",
                                f"{mean(rs, 'ack_latency_p99_us'):.0f}",
                                f"{mean(rs, 'abort_rate'):.4f}",
                                f"{mean(rs, 'pending_commits'):.0f}",
                                f"{mean(rs, 'fdatasync_per_sec'):.1f}",
                                f"{mean(rs, 'global_atomic_per_tx'):.3f}",
                                f"{mean(rs, 'frontier_bytes_per_tx'):.1f}",
                                f"{mean(rs, 'lsn_alloc_ns_per_tx'):.1f}",
                            ]
                        )
                        + " |",
                        file=f,
                    )
            print("", file=f)

        max_thread = max(thread_values)
        print("## 32-thread quick view" if max_thread == 32 else f"## {max_thread}-thread quick view", file=f)
        print("", file=f)
        print("| workload | mode | ack tps mean | p99 us | abort rate | pending | atomic/tx | frontier bytes/tx |", file=f)
        print("|---|---|---:|---:|---:|---:|---:|---:|", file=f)
        for workload in workloads:
            for mode in modes:
                rs = grouped_all.get((workload, mode, max_thread), [])
                if not rs:
                    continue
                print(
                    "| "
                    + " | ".join(
                        [
                            workload,
                            mode,
                            f"{mean(rs, 'durable_ack_tps'):.0f}",
                            f"{mean(rs, 'ack_latency_p99_us'):.0f}",
                            f"{mean(rs, 'abort_rate'):.4f}",
                            f"{mean(rs, 'pending_commits'):.0f}",
                            f"{mean(rs, 'global_atomic_per_tx'):.3f}",
                            f"{mean(rs, 'frontier_bytes_per_tx'):.1f}",
                        ]
                    )
                    + " |",
                    file=f,
                )


def write_total_budget_report(path, rows, modes, total_values, workload, title):
    grouped = {}
    for r in rows:
        grouped.setdefault((r["mode"], int(r["total_threads"])), []).append(r)

    throughput_svg = path.with_name(path.stem + "_throughput.svg")
    p99_svg = path.with_name(path.stem + "_p99.svg")
    pending_svg = path.with_name(path.stem + "_pending.svg")
    write_svg(throughput_svg, grouped, modes, total_values, "total_threads",
              "durable_ack_tps", title + " throughput",
              "durable ack tx/s")
    write_svg(p99_svg, grouped, modes, total_values, "total_threads",
              "ack_latency_p99_us", title + " p99 ack latency",
              "p99 us")
    write_svg(pending_svg, grouped, modes, total_values, "total_threads",
              "pending_commits", title + " pending commits",
              "pending durable commits")

    with path.open("w") as f:
        print(f"# {title}", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("## Definition", file=f)
        print("", file=f)
        print("`total active threads = worker threads + flusher/logger threads + committer threads`.", file=f)
        print("Transaction workers are pinned through `CCBENCH_CPU_LIST`; background flusher/committer threads are intentionally left unpinned and yield when idle.", file=f)
        print("Totals up to one socket's physical cores use one socket only; totals up to 48 use one logical CPU per physical core; 96 uses SMT siblings as well.", file=f)
        print("These runs use `numactl --interleave=all` unless explicitly disabled.", file=f)
        print("", file=f)
        print(f"workload: `{workload}` - {WORKLOAD_PRESETS[workload]['description']}", file=f)
        print("", file=f)
        print(f"![throughput]({throughput_svg.name})", file=f)
        print("", file=f)
        print(f"![p99]({p99_svg.name})", file=f)
        print("", file=f)
        print(f"![pending]({pending_svg.name})", file=f)
        print("", file=f)
        print("## Summary", file=f)
        print("", file=f)
        print("| total | mode | worker | flusher | committer | cpu policy | ack tps mean | ack tps stdev | p99 us | pending | fdatasync/s | atomic/tx | frontier bytes/tx |", file=f)
        print("|---:|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for total in total_values:
            for mode in modes:
                rs = grouped.get((mode, total), [])
                if not rs:
                    continue
                first = rs[0]
                print(
                    "| "
                    + " | ".join(
                        [
                            str(total),
                            mode,
                            first.get("worker_threads", first.get("thread_num", "")),
                            first.get("logger_threads", "0"),
                            first.get("committer_threads", "0"),
                            first.get("cpu_policy", ""),
                            f"{mean(rs, 'durable_ack_tps'):.0f}",
                            f"{stdev(rs, 'durable_ack_tps'):.1f}",
                            f"{mean(rs, 'ack_latency_p99_us'):.0f}",
                            f"{mean(rs, 'pending_commits'):.0f}",
                            f"{mean(rs, 'fdatasync_per_sec'):.1f}",
                            f"{mean(rs, 'global_atomic_per_tx'):.3f}",
                            f"{mean(rs, 'frontier_bytes_per_tx'):.1f}",
                        ]
                    )
                    + " |",
                    file=f,
                )
        print("", file=f)
        print("## CPU Lists", file=f)
        print("", file=f)
        print("| total | cpu policy | CPU list |", file=f)
        print("|---:|---|---|", file=f)
        seen = set()
        for r in rows:
            key = (r["total_threads"], r.get("cpu_policy", ""), r.get("cpu_list", ""))
            if key in seen:
                continue
            seen.add(key)
            print(f"| {key[0]} | {key[1]} | `{key[2]}` |", file=f)


def fixed_total_resources(args, mode):
    if args.fixed_total_threads <= 0:
        return (
            args.thread_num,
            args.logger_num,
            args.committer_num,
            None,
            "",
            args.thread_num,
        )
    alloc = total_budget_allocation(args.fixed_total_threads, mode)
    if alloc is None:
        raise ValueError(f"mode={mode} cannot run with total={args.fixed_total_threads}")
    worker, logger_num, committer_num = alloc
    cpus, policy = cpu_list_for_total(args.fixed_total_threads)
    return worker, logger_num, committer_num, cpus, policy, args.fixed_total_threads


def run_total_budget_experiment(args, root_out, stamp):
    modes = [m for m in args.total_budget_modes.split(",") if m]
    totals = parse_list(args.total_values)
    workload = args.total_workload
    if workload not in WORKLOAD_PRESETS:
        raise ValueError(f"unknown total workload: {workload}")
    preset = WORKLOAD_PRESETS[workload]
    out_dir = root_out / "total_budget"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(args.repeats):
        for total in totals:
            cpus, policy = cpu_list_for_total(total)
            for mode in modes:
                alloc = total_budget_allocation(total, mode)
                if alloc is None:
                    print(f"total_budget skip repeat={repeat} total={total} mode={mode}")
                    continue
                worker, logger_num, committer_num = alloc
                row = run_case(
                    out_dir,
                    mode,
                    repeat,
                    worker,
                    args.seconds,
                    preset["ycsb_max_ope"],
                    preset["ycsb_rratio"],
                    logger_num,
                    args.group_size,
                    args.flush_us,
                    args.max_pending,
                    remote_ppm=preset["remote_ppm"],
                    workload_kind=preset["binary"],
                    workload_mode=workload,
                    committer_num=committer_num,
                    total_threads=total,
                    cpu_list=cpus,
                    cpu_policy=policy,
                    use_numactl=args.numactl_interleave,
                )
                rows.append(row)
                print(
                    "total_budget "
                    f"repeat={repeat} total={total} mode={mode} "
                    f"worker={worker} logger={logger_num} committer={committer_num} "
                    f"ack_tps={row['durable_ack_tps']}"
                )
    csv_path = out_dir / f"ermia_cstamp_pwal_total_budget_{stamp}.csv"
    write_csv(csv_path, rows)
    present_modes = sorted({r["mode"] for r in rows}, key=MODE_ORDER.index)
    present_totals = [
        total for total in totals if any(int(r["total_threads"]) == total for r in rows)
    ]
    write_total_budget_report(
        csv_path.with_suffix(".md"),
        rows,
        present_modes,
        present_totals,
        workload,
        "ERMIA Cstamp-PWAL total-thread-budget scaling",
    )


def run_workloads_experiment(args, root_out, stamp):
    modes = [
        "ermia_no_durability",
        "ermia_pwal_group_global_prefix",
        "ermia_async_global_lsn_prefix",
        "ermia_async_dep_frontier_lsn",
        "ermia_async_dep_frontier_cstamp",
    ]
    threads = parse_list(args.threads)
    workloads = parse_workloads(args.workloads)
    out_dir = root_out / "workloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(args.repeats):
        for workload in workloads:
            preset = WORKLOAD_PRESETS[workload]
            for th in threads:
                for mode in modes:
                    row = run_case(
                        out_dir,
                        mode,
                        repeat,
                        th,
                        args.seconds,
                        preset["ycsb_max_ope"],
                        preset["ycsb_rratio"],
                        args.logger_num,
                        args.group_size,
                        args.flush_us,
                        args.max_pending,
                        remote_ppm=preset["remote_ppm"],
                        workload_kind=preset["binary"],
                        workload_mode=workload,
                        committer_num=args.committer_num,
                    )
                    rows.append(row)
                    print(
                        "workloads "
                        f"repeat={repeat} workload={workload} mode={mode} "
                        f"th={th} ack_tps={row['durable_ack_tps']}"
                    )
    csv_path = out_dir / f"ermia_cstamp_pwal_workloads_{stamp}.csv"
    write_csv(csv_path, rows)
    modes = sorted({r["mode"] for r in rows}, key=MODE_ORDER.index)
    write_workload_report(
        csv_path.with_suffix(".md"),
        rows,
        modes,
        threads,
        workloads,
        "ERMIA Cstamp-PWAL workload matrix",
    )


def sort_value(v):
    try:
        return (0, float(v))
    except ValueError:
        return (1, v)


def write_sweep_report(path, rows, title, group_columns):
    grouped = {}
    for r in rows:
        key = tuple(r.get(c, "") for c in group_columns)
        grouped.setdefault(key, []).append(r)
    with path.open("w") as f:
        print(f"# {title}", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        headers = group_columns + [
            "ack tps mean",
            "ack tps stdev",
            "p99 us",
            "pending",
            "fdatasync/s",
            "atomic/tx",
            "frontier bytes/tx",
            "lsn alloc ns/tx",
        ]
        print("| " + " | ".join(headers) + " |", file=f)
        print("|" + "|".join(["---"] * len(headers)) + "|", file=f)
        for key, rs in sorted(grouped.items(), key=lambda item: tuple(sort_value(v) for v in item[0])):
            values = list(key) + [
                f"{mean(rs, 'durable_ack_tps'):.0f}",
                f"{stdev(rs, 'durable_ack_tps'):.1f}",
                f"{mean(rs, 'ack_latency_p99_us'):.0f}",
                f"{mean(rs, 'pending_commits'):.0f}",
                f"{mean(rs, 'fdatasync_per_sec'):.1f}",
                f"{mean(rs, 'global_atomic_per_tx'):.3f}",
                f"{mean(rs, 'frontier_bytes_per_tx'):.1f}",
                f"{mean(rs, 'lsn_alloc_ns_per_tx'):.1f}",
            ]
            print("| " + " | ".join(values) + " |", file=f)


def write_pareto_svg(path, rows, title, label_key):
    grouped = {}
    for r in rows:
        key = (r.get("workload_mode", "custom"), r["mode"], r.get(label_key, ""))
        grouped.setdefault(key, []).append(r)
    points = []
    for (workload, mode, label), rs in grouped.items():
        points.append(
            {
                "workload": workload,
                "mode": mode,
                "label": label,
                "x": mean(rs, "ack_latency_p99_us"),
                "y": mean(rs, "durable_ack_tps"),
            }
        )
    width = 980
    height = 560
    left = 96
    right = 36
    top = 52
    bottom = 92
    plot_w = width - left - right
    plot_h = height - top - bottom
    xmax = max([p["x"] for p in points] + [1.0]) * 1.12
    ymax = max([p["y"] for p in points] + [1.0]) * 1.12

    def x_pos(x):
        return left + (x / xmax) * plot_w

    def y_pos(y):
        return top + plot_h - (y / ymax) * plot_h

    with path.open("w") as f:
        print('<?xml version="1.0" encoding="UTF-8"?>', file=f)
        print(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', file=f)
        print('<rect width="100%" height="100%" fill="white"/>', file=f)
        print(f'<text x="{left}" y="30" font-family="sans-serif" font-size="20" font-weight="700">{title}</text>', file=f)
        print(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#333"/>', file=f)
        print(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#333"/>', file=f)
        for i in range(6):
            x = xmax * i / 5
            px = x_pos(x)
            print(f'<line x1="{px:.1f}" y1="{top}" x2="{px:.1f}" y2="{top + plot_h}" stroke="#eeeeee"/>', file=f)
            print(f'<text x="{px:.1f}" y="{top + plot_h + 22}" text-anchor="middle" font-family="sans-serif" font-size="12">{x:.0f}</text>', file=f)
            y = ymax * i / 5
            py = y_pos(y)
            print(f'<line x1="{left}" y1="{py:.1f}" x2="{left + plot_w}" y2="{py:.1f}" stroke="#eeeeee"/>', file=f)
            print(f'<text x="{left - 10}" y="{py + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="12">{y:.0f}</text>', file=f)
        print(f'<text x="{left + plot_w / 2:.1f}" y="{height - 28}" text-anchor="middle" font-family="sans-serif" font-size="14">p99 durable ack latency (us)</text>', file=f)
        print(f'<text x="18" y="{top + plot_h / 2:.1f}" text-anchor="middle" font-family="sans-serif" font-size="14" transform="rotate(-90 18 {top + plot_h / 2:.1f})">durable ack tx/s</text>', file=f)
        for pnt in points:
            color = COLORS.get(pnt["mode"], "#555")
            px = x_pos(pnt["x"])
            py = y_pos(pnt["y"])
            print(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{color}"/>', file=f)
            print(f'<text x="{px + 7:.1f}" y="{py - 7:.1f}" font-family="sans-serif" font-size="11">{pnt["label"]}</text>', file=f)
        lx = left + 12
        ly = top + 18
        legend_modes = sorted({p["mode"] for p in points}, key=lambda m: MODE_ORDER.index(m) if m in MODE_ORDER else 999)
        for i, mode in enumerate(legend_modes):
            y = ly + i * 22
            print(f'<rect x="{lx}" y="{y - 10}" width="14" height="14" fill="{COLORS.get(mode, "#555")}"/>', file=f)
            print(f'<text x="{lx + 22}" y="{y + 2}" font-family="sans-serif" font-size="13">{mode}</text>', file=f)
        print("</svg>", file=f)


def run_ratio_experiment(args, root_out, stamp):
    modes = [
        "ermia_async_global_lsn_prefix",
        "ermia_async_dep_frontier_cstamp",
    ]
    workloads = parse_workloads(args.ratio_workloads)
    logger_nums = parse_list(args.logger_nums)
    committer_nums = parse_list(args.committer_nums)
    out_dir = root_out / "ratio"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(args.repeats):
        for workload in workloads:
            preset = WORKLOAD_PRESETS[workload]
            for logger_num in logger_nums:
                for committer_num in committer_nums:
                    for mode in modes:
                        row = run_case(
                            out_dir,
                            mode,
                            repeat,
                            args.thread_num,
                            args.seconds,
                            preset["ycsb_max_ope"],
                            preset["ycsb_rratio"],
                            logger_num,
                            args.group_size,
                            args.flush_us,
                            args.max_pending,
                            remote_ppm=preset["remote_ppm"],
                            workload_kind=preset["binary"],
                            workload_mode=workload,
                            committer_num=committer_num,
                        )
                        rows.append(row)
                        print(
                            "ratio "
                            f"repeat={repeat} workload={workload} mode={mode} "
                            f"threads={args.thread_num} logger={logger_num} "
                            f"committer={committer_num} ack_tps={row['durable_ack_tps']}"
                        )
    csv_path = out_dir / f"ermia_cstamp_pwal_ratio_{stamp}.csv"
    write_csv(csv_path, rows)
    write_sweep_report(
        csv_path.with_suffix(".md"),
        rows,
        "ERMIA Cstamp-PWAL worker/flusher/committer ratio sweep",
        ["workload_mode", "mode", "thread_num", "logger_num", "committer_num"],
    )


def run_max_pending_experiment(args, root_out, stamp):
    modes = [
        "ermia_async_global_lsn_prefix",
        "ermia_async_dep_frontier_cstamp",
    ]
    workloads = parse_workloads(args.pareto_workloads)
    pending_values = parse_list(args.max_pending_values)
    out_dir = root_out / "max_pending"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(args.repeats):
        for workload in workloads:
            preset = WORKLOAD_PRESETS[workload]
            for max_pending in pending_values:
                for mode in modes:
                    worker, logger_num, committer_num, cpus, policy, total_threads = (
                        fixed_total_resources(args, mode)
                    )
                    row = run_case(
                        out_dir,
                        mode,
                        repeat,
                        worker,
                        args.seconds,
                        preset["ycsb_max_ope"],
                        preset["ycsb_rratio"],
                        logger_num,
                        args.group_size,
                        args.flush_us,
                        max_pending,
                        remote_ppm=preset["remote_ppm"],
                        workload_kind=preset["binary"],
                        workload_mode=workload,
                        committer_num=committer_num,
                        total_threads=total_threads,
                        cpu_list=cpus,
                        cpu_policy=policy,
                        use_numactl=args.numactl_interleave and cpus is not None,
                    )
                    rows.append(row)
                    print(
                        "max_pending "
                        f"repeat={repeat} workload={workload} mode={mode} "
                        f"total={total_threads} worker={worker} max_pending={max_pending} "
                        f"ack_tps={row['durable_ack_tps']}"
                    )
    csv_path = out_dir / f"ermia_cstamp_pwal_max_pending_{stamp}.csv"
    write_csv(csv_path, rows)
    report_path = csv_path.with_suffix(".md")
    pareto_path = csv_path.with_name(csv_path.stem + "_pareto.svg")
    write_sweep_report(
        report_path,
        rows,
        "ERMIA Cstamp-PWAL max_pending sweep",
        ["workload_mode", "mode", "total_threads", "thread_num", "logger_num", "committer_num", "max_pending"],
    )
    write_pareto_svg(
        pareto_path,
        rows,
        "ERMIA Cstamp-PWAL max_pending Pareto",
        "max_pending",
    )
    with report_path.open("a") as f:
        print("", file=f)
        print("## Pareto", file=f)
        print("", file=f)
        print(f"![pareto]({pareto_path.name})", file=f)


def write_breakdown_report(path, rows, title):
    grouped = {}
    for r in rows:
        key = (r["mode"], int(r["max_pending"]))
        grouped.setdefault(key, []).append(r)

    max_values = sorted({int(r["max_pending"]) for r in rows})

    with path.open("w") as f:
        print(f"# {title}", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("workload: YCSB-B, threads: 32, logger_num: 8, committer_num: 1, group_size: 8, flush_us: 100", file=f)
        print("", file=f)
        print("## Summary", file=f)
        print("", file=f)
        headers = [
            "mode",
            "max_pending",
            "ack tps",
            "logical tps",
            "p50 us",
            "p90 us",
            "p99 us",
            "p999 us",
            "max us",
            "pending",
            "logical-acked",
        ]
        print("| " + " | ".join(headers) + " |", file=f)
        print("|" + "|".join(["---"] * len(headers)) + "|", file=f)
        for (mode, max_pending), rs in sorted(
            grouped.items(),
            key=lambda item: (
                int(item[0][1]),
                MODE_ORDER.index(item[0][0]) if item[0][0] in MODE_ORDER else 999,
            ),
        ):
            values = [
                mode,
                str(max_pending),
                f"{mean(rs, 'durable_ack_tps'):.0f}",
                f"{mean(rs, 'logical_tps'):.0f}",
                f"{mean(rs, 'ack_latency_p50_us'):.0f}",
                f"{mean(rs, 'ack_latency_p90_us'):.0f}",
                f"{mean(rs, 'ack_latency_p99_us'):.0f}",
                f"{mean(rs, 'ack_latency_p999_us'):.0f}",
                f"{mean(rs, 'ack_latency_max_us'):.0f}",
                f"{mean(rs, 'pending_commits'):.0f}",
                f"{mean(rs, 'logical_minus_acked'):.0f}",
            ]
            print("| " + " | ".join(values) + " |", file=f)
        print("", file=f)

        for max_pending in sorted(max_values, reverse=True):
            print(f"## Per-transaction cost breakdown max_pending={max_pending}", file=f)
            print("", file=f)
            components = [
                ("WAL enqueue ns/tx", "wal_enqueue_ns_per_tx"),
                ("frontier collect ns/tx", "frontier_collect_ns_per_tx"),
                ("frontier merge ns/tx", "frontier_merge_ns_per_tx"),
                ("frontier publish ns/tx", "frontier_publish_ns_per_tx"),
                ("version install ns/tx", "version_install_ns_per_tx"),
                ("waitlist registration ns/tx", "waitlist_registration_ns_per_tx"),
                ("committer event ns/tx", "committer_event_ns_per_tx"),
                ("ready queue push ns/tx", "ready_queue_push_ns_per_tx"),
                ("ack process ns/tx", "ack_process_ns_per_tx"),
                ("write ns/tx", "write_ns_per_tx"),
                ("fdatasync ns/tx", "fdatasync_ns_per_tx"),
                ("worker stall ns/tx", "worker_stall_ns_per_tx"),
                ("queue wait us/acked tx", "queue_wait_us_per_acked_tx"),
                ("p99 durable ack us", "ack_latency_p99_us"),
            ]
            base = "ermia_async_global_lsn_prefix"
            dep = "ermia_async_dep_frontier_cstamp"
            print("| component | global prefix | dep frontier cstamp | delta |", file=f)
            print("|---|---:|---:|---:|", file=f)
            for label, metric in components:
                g = mean(grouped.get((base, max_pending), []), metric)
                d = mean(grouped.get((dep, max_pending), []), metric)
                print(f"| {label} | {g:.2f} | {d:.2f} | {d - g:.2f} |", file=f)
            print("", file=f)

            print(f"## Metadata / waitlist / batching max_pending={max_pending}", file=f)
            print("", file=f)
            headers = [
                "mode",
                "frontier bytes/tx",
                "entries/tx",
                "nonzero entries/tx",
                "read updates/tx",
                "write updates/tx",
                "alloc/tx",
                "shared_ptr/tx",
                "waitlist regs/tx",
                "waitlist pops/tx",
                "conditions/tx",
                "max waitlist",
                "avg waitlist",
                "max ready q",
                "avg ready q",
                "fdatasync/s",
                "commits/fdatasync",
                "avg batch",
                "bytes/s",
            ]
            print("| " + " | ".join(headers) + " |", file=f)
            print("|" + "|".join(["---"] * len(headers)) + "|", file=f)
            modes = sorted(
                {m for m, pnd in grouped if pnd == max_pending},
                key=lambda m: MODE_ORDER.index(m) if m in MODE_ORDER else 999,
            )
            for mode in modes:
                rs = grouped.get((mode, max_pending), [])
                values = [
                    mode,
                    f"{mean(rs, 'frontier_bytes_per_tx'):.1f}",
                    f"{mean(rs, 'frontier_entries_per_tx'):.2f}",
                    f"{mean(rs, 'frontier_nonzero_entries_per_tx'):.2f}",
                    f"{mean(rs, 'read_frontier_updates_per_tx'):.2f}",
                    f"{mean(rs, 'write_frontier_updates_per_tx'):.2f}",
                    f"{mean(rs, 'frontier_alloc_count_per_tx'):.2f}",
                    f"{mean(rs, 'frontier_shared_ptr_count_per_tx'):.2f}",
                    f"{mean(rs, 'waitlist_registrations_per_tx'):.2f}",
                    f"{mean(rs, 'waitlist_pops_per_tx'):.2f}",
                    f"{mean(rs, 'waiting_conditions_per_tx'):.2f}",
                    f"{mean(rs, 'max_waitlist_len'):.0f}",
                    f"{mean(rs, 'avg_waitlist_len'):.0f}",
                    f"{mean(rs, 'max_ready_queue_len'):.0f}",
                    f"{mean(rs, 'avg_ready_queue_len'):.0f}",
                    f"{mean(rs, 'fdatasync_per_sec'):.1f}",
                    f"{mean(rs, 'commits_per_fdatasync'):.2f}",
                    f"{mean(rs, 'avg_batch_size'):.2f}",
                    f"{mean(rs, 'bytes_flushed_per_sec'):.0f}",
                ]
                print("| " + " | ".join(values) + " |", file=f)
            print("", file=f)


def run_breakdown_experiment(args, root_out, stamp):
    modes = [m for m in args.breakdown_modes.split(",") if m]
    pending_values = parse_list(args.max_pending_values)
    out_dir = root_out / "breakdown"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    preset = WORKLOAD_PRESETS["ycsb_b"]
    for repeat in range(args.repeats):
        for max_pending in pending_values:
            for mode in modes:
                row = run_case(
                    out_dir,
                    mode,
                    repeat,
                    args.thread_num,
                    args.seconds,
                    preset["ycsb_max_ope"],
                    preset["ycsb_rratio"],
                    args.logger_num,
                    args.group_size,
                    args.flush_us,
                    max_pending,
                    remote_ppm=preset["remote_ppm"],
                    workload_kind=preset["binary"],
                    workload_mode="ycsb_b",
                    committer_num=args.committer_num,
                )
                rows.append(row)
                print(
                    "breakdown "
                    f"repeat={repeat} mode={mode} threads={args.thread_num} "
                    f"max_pending={max_pending} ack_tps={row['durable_ack_tps']}"
                )
    csv_path = out_dir / f"ermia_cstamp_pwal_breakdown_{stamp}.csv"
    write_csv(csv_path, rows)
    write_breakdown_report(
        csv_path.with_suffix(".md"),
        rows,
        "ERMIA Cstamp-PWAL throughput gap breakdown",
    )


def run_correctness(out_dir):
    stdout_path = out_dir / "ermia_cstamp_pwal_correctness.out"
    stderr_path = out_dir / "ermia_cstamp_pwal_correctness.err"
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.run([str(CORRECTNESS_EXE)], cwd=ROOT, stdout=out, stderr=err)
    md = out_dir / "ermia_cstamp_pwal_correctness.md"
    md.write_text(stdout_path.read_text(errors="replace"))
    if proc.returncode != 0:
        raise RuntimeError(f"correctness test failed: {stderr_path}")


def experiment_matrix(args):
    if args.experiment == "abort0":
        modes = [
            "ermia_no_durability",
            "ermia_pwal_group_global_prefix",
            "ermia_async_global_lsn_prefix",
            "ermia_async_dep_frontier_lsn",
            "ermia_async_dep_frontier_cstamp",
        ]
        threads = parse_list(args.threads)
        for th in threads:
            for mode in modes:
                yield mode, th, 0, 0
    elif args.experiment == "dependency":
        modes = [
            "ermia_async_global_lsn_prefix",
            "ermia_async_dep_frontier_lsn",
            "ermia_async_dep_frontier_cstamp",
        ]
        for remote in parse_list(args.remote_ppm):
            for mode in modes:
                yield mode, args.thread_num, remote, 0
    elif args.experiment == "straggler":
        modes = [
            "ermia_async_global_lsn_prefix",
            "ermia_async_dep_frontier_cstamp",
        ]
        for remote in parse_list(args.remote_ppm):
            for mode in modes:
                yield mode, args.thread_num, remote, 0
    elif args.experiment == "cstamp":
        modes = [
            "ermia_async_dep_frontier_lsn",
            "ermia_async_dep_frontier_cstamp",
        ]
        threads = parse_list(args.threads)
        for condition in (0, 1):
            for th in threads:
                for mode in modes:
                    yield mode, th, 0, condition
    else:
        raise ValueError(args.experiment)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--experiment", choices=["abort0", "dependency", "straggler", "cstamp", "workloads", "ratio", "max_pending", "breakdown", "total_budget", "all"], default=os.environ.get("ERMIA_CSTAMP_EXPERIMENT", "abort0"))
    p.add_argument("--seconds", type=int, default=int(os.environ.get("ERMIA_CSTAMP_SECONDS", "5")))
    p.add_argument("--repeats", type=int, default=int(os.environ.get("ERMIA_CSTAMP_REPEATS", "5")))
    p.add_argument("--threads", default=os.environ.get("ERMIA_CSTAMP_THREADS", "1,2,4,8,16,32"))
    p.add_argument("--thread-num", type=int, default=int(os.environ.get("ERMIA_CSTAMP_THREAD_NUM", "32")))
    p.add_argument("--remote-ppm", default=os.environ.get("ERMIA_CSTAMP_REMOTE_PPM", "0,10000,50000,100000,500000,1000000"))
    p.add_argument("--workloads", default=os.environ.get("ERMIA_CSTAMP_WORKLOADS", "abort0_write1,ycsb_a,ycsb_b"))
    p.add_argument("--ratio-workloads", default=os.environ.get("ERMIA_CSTAMP_RATIO_WORKLOADS", "abort0_write1,ycsb_b"))
    p.add_argument("--pareto-workloads", default=os.environ.get("ERMIA_CSTAMP_PARETO_WORKLOADS", "ycsb_b"))
    p.add_argument("--cstamp-workload", default=os.environ.get("ERMIA_CSTAMP_CSTAMP_WORKLOAD", "ycsb_b"))
    p.add_argument("--total-values", default=os.environ.get("ERMIA_CSTAMP_TOTAL_VALUES", "1,2,4,8,16,24,32,48,96"))
    p.add_argument("--total-workload", default=os.environ.get("ERMIA_CSTAMP_TOTAL_WORKLOAD", "ycsb_b"))
    p.add_argument("--total-budget-modes", default=os.environ.get(
        "ERMIA_CSTAMP_TOTAL_BUDGET_MODES",
        ",".join(TOTAL_BUDGET_MODES),
    ))
    p.add_argument("--logger-nums", default=os.environ.get("ERMIA_CSTAMP_LOGGER_NUMS", "1,2,4,8"))
    p.add_argument("--committer-nums", default=os.environ.get("ERMIA_CSTAMP_COMMITTER_NUMS", "1,2"))
    p.add_argument("--max-pending-values", default=os.environ.get("ERMIA_CSTAMP_MAX_PENDING_VALUES", "1024,4096,16384,65536"))
    p.add_argument("--breakdown-modes", default=os.environ.get(
        "ERMIA_CSTAMP_BREAKDOWN_MODES",
        "ermia_async_global_lsn_prefix,ermia_async_dep_frontier_cstamp,"
        "ermia_async_dep_frontier_cstamp_no_publish,"
        "ermia_async_dep_frontier_cstamp_zero_dep,"
        "ermia_async_dep_frontier_cstamp_prealloc",
    ))
    p.add_argument("--logger-num", type=int, default=int(os.environ.get("ERMIA_CSTAMP_LOGGER_NUM", "4")))
    p.add_argument("--committer-num", type=int, default=int(os.environ.get("ERMIA_CSTAMP_COMMITTER_NUM", "1")))
    p.add_argument("--group-size", type=int, default=int(os.environ.get("ERMIA_CSTAMP_GROUP_SIZE", "8")))
    p.add_argument("--flush-us", type=int, default=int(os.environ.get("ERMIA_CSTAMP_FLUSH_US", "100")))
    p.add_argument("--max-pending", type=int, default=int(os.environ.get("ERMIA_CSTAMP_MAX_PENDING", "65536")))
    p.add_argument("--ycsb-max-ope", type=int, default=int(os.environ.get("ERMIA_CSTAMP_YCSB_MAX_OPE", "1")))
    p.add_argument("--ycsb-rratio", type=int, default=int(os.environ.get("ERMIA_CSTAMP_YCSB_RRATIO", "0")))
    p.add_argument("--straggler-logger", type=int, default=int(os.environ.get("ERMIA_CSTAMP_STRAGGLER_LOGGER", "3")))
    p.add_argument("--straggler-sleep-us", type=int, default=int(os.environ.get("ERMIA_CSTAMP_STRAGGLER_SLEEP_US", "1000")))
    p.add_argument("--fixed-total-threads", type=int, default=int(os.environ.get("ERMIA_CSTAMP_FIXED_TOTAL_THREADS", "0")))
    p.add_argument("--numactl-interleave", dest="numactl_interleave", action="store_true", default=os.environ.get("ERMIA_CSTAMP_NUMACTL_INTERLEAVE", "1") != "0")
    p.add_argument("--no-numactl-interleave", dest="numactl_interleave", action="store_false")
    p.add_argument("--skip-build", action="store_true")
    args = p.parse_args()

    if not args.skip_build:
        build()

    experiments = ["abort0", "dependency", "straggler", "cstamp", "workloads", "total_budget"] if args.experiment == "all" else [args.experiment]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    root_out = RESULTS / f"ermia_cstamp_pwal_{stamp}"
    root_out.mkdir(parents=True, exist_ok=True)
    run_correctness(root_out)

    for exp in experiments:
        if exp == "workloads":
            run_workloads_experiment(args, root_out, stamp)
            continue
        if exp == "ratio":
            run_ratio_experiment(args, root_out, stamp)
            continue
        if exp == "max_pending":
            run_max_pending_experiment(args, root_out, stamp)
            continue
        if exp == "breakdown":
            run_breakdown_experiment(args, root_out, stamp)
            continue
        if exp == "total_budget":
            run_total_budget_experiment(args, root_out, stamp)
            continue
        args.experiment = exp
        out_dir = root_out / exp
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for repeat in range(args.repeats):
            for mode, th, remote, condition in experiment_matrix(args):
                skip_fdatasync = 0
                group_size = args.group_size
                flush_us = args.flush_us
                label_remote = remote
                straggler_logger = -1
                straggler_sleep_us = 0
                ycsb_rratio = args.ycsb_rratio
                ycsb_max_ope = args.ycsb_max_ope
                workload_kind = "abort0"
                workload_mode = "custom"
                if exp == "dependency":
                    ycsb_rratio = int(os.environ.get("ERMIA_CSTAMP_DEP_RRATIO", "50"))
                    ycsb_max_ope = int(os.environ.get("ERMIA_CSTAMP_DEP_MAX_OPE", "2"))
                if exp == "straggler":
                    ycsb_rratio = int(os.environ.get("ERMIA_CSTAMP_STR_RRATIO", "50"))
                    ycsb_max_ope = int(os.environ.get("ERMIA_CSTAMP_STR_MAX_OPE", "2"))
                    straggler_logger = args.straggler_logger
                    straggler_sleep_us = args.straggler_sleep_us
                if exp == "cstamp" and condition == 1:
                    skip_fdatasync = 1
                    group_size = int(os.environ.get("ERMIA_CSTAMP_IO_LIGHT_GROUP_SIZE", "64"))
                    flush_us = int(os.environ.get("ERMIA_CSTAMP_IO_LIGHT_FLUSH_US", "1000"))
                if exp == "cstamp":
                    if args.cstamp_workload not in WORKLOAD_PRESETS:
                        raise ValueError(f"unknown cstamp workload: {args.cstamp_workload}")
                    preset = WORKLOAD_PRESETS[args.cstamp_workload]
                    workload_kind = preset["binary"]
                    workload_mode = args.cstamp_workload
                    ycsb_rratio = preset["ycsb_rratio"]
                    ycsb_max_ope = preset["ycsb_max_ope"]
                case_thread = th
                logger_num = args.logger_num
                committer_num = args.committer_num
                cpu_list = None
                cpu_policy = ""
                total_threads = th
                if args.fixed_total_threads > 0:
                    case_thread, logger_num, committer_num, cpu_list, cpu_policy, total_threads = (
                        fixed_total_resources(args, mode)
                    )
                row = run_case(out_dir, mode, repeat, case_thread, args.seconds, ycsb_max_ope,
                               ycsb_rratio, logger_num, group_size, flush_us,
                               args.max_pending, remote_ppm=label_remote,
                               straggler_logger=straggler_logger,
                               straggler_sleep_us=straggler_sleep_us,
                               skip_fdatasync=skip_fdatasync,
                               committer_num=committer_num,
                               total_threads=total_threads,
                               cpu_list=cpu_list,
                               cpu_policy=cpu_policy,
                               use_numactl=args.numactl_interleave and cpu_list is not None,
                               workload_kind=workload_kind,
                               workload_mode=workload_mode)
                if exp == "cstamp":
                    row["mode_base"] = row["mode"]
                    row["io_condition"] = "io_light" if condition == 1 else "real_io"
                    row["mode"] = f"{row['mode_base']}_{row['io_condition']}"
                rows.append(row)
                print(
                    f"{exp} repeat={repeat} mode={mode} total={total_threads} "
                    f"worker={case_thread} remote={remote} ack_tps={row['durable_ack_tps']}"
                )
        csv_path = out_dir / f"ermia_cstamp_pwal_{exp}_{stamp}.csv"
        write_csv(csv_path, rows)
        if exp == "dependency" or exp == "straggler":
            x_values = [p / 1_000_000 for p in parse_list(args.remote_ppm)]
            x_key = "remote_read_prob"
            modes = sorted({r["mode"] for r in rows}, key=MODE_ORDER.index)
        else:
            if args.fixed_total_threads > 0:
                x_values = [args.fixed_total_threads]
                x_key = "total_threads"
            else:
                x_values = parse_list(args.threads)
                x_key = "thread_num"
            modes = sorted({r["mode"] for r in rows}, key=MODE_ORDER.index)
        write_report(csv_path.with_suffix(".md"), rows, modes, x_values, x_key,
                     f"ERMIA Cstamp-PWAL {exp}")

    print(root_out)


if __name__ == "__main__":
    main()
