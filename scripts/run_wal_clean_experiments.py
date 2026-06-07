#!/usr/bin/env python3
import csv
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

THREADS = [1, 2, 4, 8, 16, 32]
EXTIME = 10
YCSB_PROTOS = ["ermia_wal", "ermia_pwal"]
FRAMEWORK_MODES = ["wal", "pwal"]


def metric(text, name):
    m = re.search(rf"^{re.escape(name)}:\s*([^\s]+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def run_abort0(root, logs, wal_dir, proto, th):
    exe = root / "build" / "cc" / proto / f"ycsb_abort0_{proto}.exe"
    out = logs / f"abort0_{proto}_{th}.out"
    err = logs / f"abort0_{proto}_{th}.err"
    cmd = [str(exe), f"--extime={EXTIME}", f"--thread_num={th}",
           "--ycsb_tuple_num=100000", "--ycsb_max_ope=10", "--ycsb_rratio=50", "--ycsb_zipf_skew=0"]
    env = dict(os.environ, CCBENCH_WAL_DIR=str(wal_dir))
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, cwd=root, stdout=stdout, stderr=stderr, env=env)
    text = out.read_text(errors="replace")
    return {"experiment":"abort0_ycsb", "mode":proto, "thread_num":th, "exit_code":proc.returncode,
            "throughput_tps":metric(text,"throughput[tps]"), "abort_rate":metric(text,"abort_rate"),
            "commit_counts":metric(text,"commit_counts_"), "stdout_log":str(out), "stderr_log":str(err)}


def run_framework(root, logs, wal_dir, mode, th):
    exe = root / "build" / "wal_framework_microbench.exe"
    out = logs / f"framework_{mode}_{th}.out"
    err = logs / f"framework_{mode}_{th}.err"
    cmd = [str(exe), f"--mode={mode}", f"--threads={th}", f"--seconds={EXTIME}",
           "--writes_per_tx=1", "--payload_bytes=128", f"--output_root={wal_dir}"]
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, cwd=root, stdout=stdout, stderr=stderr)
    text = out.read_text(errors="replace")
    return {"experiment":"wal_framework", "mode":mode, "thread_num":th, "exit_code":proc.returncode,
            "throughput_tps":metric(text,"throughput_tps"), "abort_rate":"", "commit_counts":metric(text,"commit_count"),
            "stdout_log":str(out), "stderr_log":str(err)}


def main():
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = root / "results" / f"wal_clean_abort0_framework_{stamp}.txt"
    logs = root / "results" / f"wal_clean_logs_{stamp}"
    wal_dir = root / "results" / f"wal_clean_files_{stamp}"
    logs.mkdir(parents=True, exist_ok=True)
    wal_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for th in THREADS:
        for proto in YCSB_PROTOS:
            print(f"RUN abort0 {proto} threads={th}", flush=True)
            rows.append(run_abort0(root, logs, wal_dir, proto, th))
        for mode in FRAMEWORK_MODES:
            print(f"RUN framework {mode} threads={th}", flush=True)
            rows.append(run_framework(root, logs, wal_dir, mode, th))
    with result.open("w") as f:
        print("WAL clean-world experiments", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("threads: " + " ".join(map(str, THREADS)), file=f)
        print("abort0_ycsb: ERMIA+WAL/P-WAL with worker-partitioned YCSB key ranges", file=f)
        print("wal_framework: no CC, synthetic tx log generation + write/fdatasync", file=f)
        print("no injected wait or random flush delay", file=f)
        print("===== SUMMARY_CSV =====", file=f)
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(result)


if __name__ == "__main__":
    main()
