#!/usr/bin/env python3
import csv
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

THREADS = [1, 2, 4, 8, 16, 32]
SECONDS = 5
PROTOS = ["ermia_wal", "ermia_pwal"]
EXPERIMENTS = ["normal_ycsb", "abort0_ycsb"]
TX_FIELDS = [
    "read_ns", "update_ns", "insert_ns", "delete_ns", "abort_cleanup_ns",
    "maintenance_ns", "ssn_finalize_pi_ns", "ssn_finalize_eta_ns",
    "ssn_exclusion_ns", "node_validation_ns", "wal_log_ns",
    "version_install_ns", "commit_cleanup_ns", "total_accounted_ns",
]
WAL_FIELDS = [
    "payload_build_ns", "mutex_wait_ns", "write_ns", "fdatasync_ns", "notify_wait_ns",
]


def metric(text, name):
    m = re.search(rf"^{re.escape(name)}:\s*([^\s]+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def as_int(value):
    try:
        return int(value)
    except Exception:
        return 0


def as_float(value):
    try:
        return float(value)
    except Exception:
        return 0.0


def run_case(root, logs, wal_dir, experiment, proto, th):
    workload = "ycsb_abort0" if experiment == "abort0_ycsb" else "ycsb"
    exe = root / "build" / "cc" / proto / f"{workload}_{proto}.exe"
    out = logs / f"{experiment}_{proto}_{th}.out"
    err = logs / f"{experiment}_{proto}_{th}.err"
    cmd = [str(exe), f"--extime={SECONDS}", f"--thread_num={th}",
           "--ycsb_tuple_num=100000", "--ycsb_max_ope=10", "--ycsb_rratio=50", "--ycsb_zipf_skew=0"]
    env = dict(os.environ, CCBENCH_WAL_DIR=str(wal_dir))
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, cwd=root, stdout=stdout, stderr=stderr, env=env)
    text = out.read_text(errors="replace")
    actual_extime = as_float(metric(text, "actual_extime")) or SECONDS
    denom_ns = actual_extime * th * 1_000_000_000.0
    row = {
        "experiment": experiment,
        "mode": proto,
        "thread_num": th,
        "seconds": SECONDS,
        "actual_extime": actual_extime,
        "denom_thread_ns": int(denom_ns),
        "exit_code": proc.returncode,
        "throughput_tps": metric(text, "throughput[tps]"),
        "abort_rate": metric(text, "abort_rate"),
        "commit_counts": metric(text, "commit_counts_"),
        "stdout_log": str(out),
        "stderr_log": str(err),
    }
    for field in TX_FIELDS:
        row[field] = metric(text, "tx_breakdown_" + field) or "0"
        row[field + "_pct"] = f"{as_int(row[field]) / denom_ns * 100.0 if denom_ns else 0.0:.4f}"
    for field in WAL_FIELDS:
        row["wal_" + field] = metric(text, "wal_stats_" + field) or "0"
        row["wal_" + field + "_pct"] = f"{as_int(row['wal_' + field]) / denom_ns * 100.0 if denom_ns else 0.0:.4f}"
    ermia_other = sum(as_int(row[f]) for f in [
        "read_ns", "update_ns", "insert_ns", "delete_ns", "abort_cleanup_ns",
        "maintenance_ns", "node_validation_ns", "version_install_ns", "commit_cleanup_ns",
    ])
    ssn = sum(as_int(row[f]) for f in ["ssn_finalize_pi_ns", "ssn_finalize_eta_ns", "ssn_exclusion_ns"])
    wal = as_int(row["wal_log_ns"])
    accounted = ermia_other + ssn + wal
    row["group_ermia_other_ns"] = str(ermia_other)
    row["group_ssn_ns"] = str(ssn)
    row["group_wal_ns"] = str(wal)
    row["group_unaccounted_ns"] = str(max(0, int(denom_ns) - accounted))
    for f in ["group_ermia_other_ns", "group_ssn_ns", "group_wal_ns", "group_unaccounted_ns"]:
        row[f + "_pct"] = f"{as_int(row[f]) / denom_ns * 100.0 if denom_ns else 0.0:.4f}"
    return row


def main():
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = root / "results" / f"tx_breakdown_{stamp}.txt"
    logs = root / "results" / f"tx_breakdown_logs_{stamp}"
    wal_dir = root / "results" / f"tx_breakdown_wal_files_{stamp}"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for experiment in EXPERIMENTS:
        for th in THREADS:
            for proto in PROTOS:
                print(f"RUN {experiment} {proto} threads={th}", flush=True)
                rows.append(run_case(root, logs, wal_dir, experiment, proto, th))
    with result.open("w") as f:
        print("TX hierarchical breakdown", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("experiments: " + " ".join(EXPERIMENTS), file=f)
        print("protos: " + " ".join(PROTOS), file=f)
        print("threads: " + " ".join(map(str, THREADS)), file=f)
        print(f"seconds: {SECONDS}", file=f)
        print("percent denominator: actual_extime * thread_num * 1e9", file=f)
        print("===== SUMMARY_CSV =====", file=f)
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(result)


if __name__ == "__main__":
    main()
