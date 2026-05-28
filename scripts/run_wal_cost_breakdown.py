#!/usr/bin/env python3
import csv
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

THREADS = [1, 16, 32]
SECONDS = 5
ABORT0_PROTOS = ["ermia_wal", "ermia_pwal"]
FRAMEWORK_MODES = ["wal", "pwal"]
COST_FIELDS = [
    "payload_build_ns", "mutex_wait_ns", "write_ns", "fdatasync_ns", "notify_wait_ns"
]


def metric(text, name):
    match = re.search(rf"^{re.escape(name)}:\s*([^\s]+)", text, re.MULTILINE)
    return match.group(1) if match else ""


def normalize_costs(row):
    total = 0
    for field in COST_FIELDS:
        total += int(row.get(field, "0") or 0)
    row["total_accounted_ns"] = str(total)
    for field in COST_FIELDS:
        value = int(row.get(field, "0") or 0)
        row[field + "_pct"] = f"{(value / total * 100.0) if total else 0:.2f}"


def run_abort0(root, logs, wal_dir, proto, th):
    exe = root / "build" / "cc" / proto / f"ycsb_abort0_{proto}.exe"
    out = logs / f"abort0_{proto}_{th}.out"
    err = logs / f"abort0_{proto}_{th}.err"
    cmd = [str(exe), f"--extime={SECONDS}", f"--thread_num={th}",
           "--ycsb_tuple_num=100000", "--ycsb_max_ope=10", "--ycsb_rratio=50", "--ycsb_zipf_skew=0"]
    env = dict(os.environ, CCBENCH_WAL_DIR=str(wal_dir))
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, cwd=root, stdout=stdout, stderr=stderr, env=env)
    text = out.read_text(errors="replace")
    row = {
        "experiment": "abort0_ycsb",
        "mode": proto,
        "thread_num": th,
        "seconds": SECONDS,
        "exit_code": proc.returncode,
        "throughput_tps": metric(text, "throughput[tps]"),
        "abort_rate": metric(text, "abort_rate"),
        "commits": metric(text, "wal_stats_commits") or metric(text, "commit_counts_"),
        "payload_build_ns": metric(text, "wal_stats_payload_build_ns") or "0",
        "mutex_wait_ns": metric(text, "wal_stats_mutex_wait_ns") or "0",
        "write_ns": metric(text, "wal_stats_write_ns") or "0",
        "fdatasync_ns": metric(text, "wal_stats_fdatasync_ns") or "0",
        "notify_wait_ns": metric(text, "wal_stats_notify_wait_ns") or "0",
        "stdout_log": str(out),
        "stderr_log": str(err),
    }
    normalize_costs(row)
    return row


def run_framework(root, logs, wal_dir, mode, th):
    exe = root / "build" / "wal_framework_microbench.exe"
    out = logs / f"framework_{mode}_{th}.out"
    err = logs / f"framework_{mode}_{th}.err"
    cmd = [str(exe), f"--mode={mode}", f"--threads={th}", f"--seconds={SECONDS}",
           "--writes_per_tx=1", "--payload_bytes=128", f"--output_root={wal_dir}"]
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, cwd=root, stdout=stdout, stderr=stderr)
    text = out.read_text(errors="replace")
    row = {
        "experiment": "wal_framework",
        "mode": mode,
        "thread_num": th,
        "seconds": SECONDS,
        "exit_code": proc.returncode,
        "throughput_tps": metric(text, "throughput_tps"),
        "abort_rate": "",
        "commits": metric(text, "commit_count"),
        "payload_build_ns": metric(text, "cost_payload_build_ns") or "0",
        "mutex_wait_ns": metric(text, "cost_mutex_wait_ns") or "0",
        "write_ns": metric(text, "cost_write_ns") or "0",
        "fdatasync_ns": metric(text, "cost_fdatasync_ns") or "0",
        "notify_wait_ns": "0",
        "stdout_log": str(out),
        "stderr_log": str(err),
    }
    normalize_costs(row)
    return row


def main():
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = root / "results" / f"wal_cost_breakdown_{stamp}.txt"
    logs = root / "results" / f"wal_cost_logs_{stamp}"
    wal_dir = root / "results" / f"wal_cost_files_{stamp}"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for th in THREADS:
        for proto in ABORT0_PROTOS:
            print(f"RUN abort0 {proto} threads={th}", flush=True)
            rows.append(run_abort0(root, logs, wal_dir, proto, th))
        for mode in FRAMEWORK_MODES:
            print(f"RUN framework {mode} threads={th}", flush=True)
            rows.append(run_framework(root, logs, wal_dir, mode, th))
    with result.open("w") as f:
        print("WAL cost breakdown", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("threads: " + " ".join(map(str, THREADS)), file=f)
        print(f"seconds: {SECONDS}", file=f)
        print("cost fields: payload_build mutex_wait write fdatasync notify_wait", file=f)
        print("===== SUMMARY_CSV =====", file=f)
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(result)


if __name__ == "__main__":
    main()
