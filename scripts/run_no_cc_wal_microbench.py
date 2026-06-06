#!/usr/bin/env python3
import csv
import os
import re
import subprocess
import statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "build" / "no_cc_wal_microbench.exe"
SRC = ROOT / "tools" / "no_cc_wal_microbench.cc"

THREADS = [1, 2, 4, 8, 16, 32]
MODES = [
    "no_durability",
    "single_wal",
    "single_wal_group_commit",
    "pwal_per_txn_fdatasync",
    "pwal_group_commit",
    "pwal_group_commit_no_prefix",
]
SECONDS = int(os.environ.get("NO_CC_WAL_SECONDS", "2"))
REPEATS = int(os.environ.get("NO_CC_WAL_REPEATS", "1"))
WRITE_SET_SIZE = int(os.environ.get("NO_CC_WAL_WRITE_SET_SIZE", "10"))
VALUE_SIZE = int(os.environ.get("NO_CC_WAL_VALUE_SIZE", "32"))
GROUP_SIZE = int(os.environ.get("NO_CC_WAL_GROUP_SIZE", "8"))
FLUSH_US = int(os.environ.get("NO_CC_WAL_FLUSH_US", "100"))
PREALLOC_MB = int(os.environ.get("NO_CC_WAL_PREALLOC_MB", "0"))
PREALLOC_ADAPTIVE = os.environ.get("NO_CC_WAL_PREALLOC_ADAPTIVE", "0") == "1"


def build():
    EXE.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "g++",
        "-O3",
        "-std=c++17",
        "-pthread",
        str(SRC),
        "-o",
        str(EXE),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            row[m.group(1)] = m.group(2)
    return row


def logger_num_for_threads(th):
    return max(1, min(4, th // 8))


def logger_num_for_case(mode, th):
    if mode == "single_wal_group_commit":
        return 1
    return logger_num_for_threads(th)


def prealloc_mb_for_case(mode):
    if mode == "no_durability":
        return 0
    if PREALLOC_MB:
        return PREALLOC_MB
    if not PREALLOC_ADAPTIVE:
        return 0
    if mode in ("single_wal", "pwal_per_txn_fdatasync"):
        return 16
    if mode == "single_wal_group_commit":
        return 256
    return 64


def run_case(stamp, mode, th, repeat):
    out_dir = ROOT / "results" / f"no_cc_wal_microbench_{stamp}"
    wal_dir = out_dir / "wal_files"
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logger_num = logger_num_for_case(mode, th)
    prealloc_mb = prealloc_mb_for_case(mode)
    cmd = [
        str(EXE),
        f"--mode={mode}",
        f"--thread_num={th}",
        f"--seconds={SECONDS}",
        f"--write_set_size={WRITE_SET_SIZE}",
        f"--value_size={VALUE_SIZE}",
        f"--group_size={GROUP_SIZE}",
        f"--flush_us={FLUSH_US}",
        f"--logger_num={logger_num}",
        f"--prealloc_mb={prealloc_mb}",
        f"--wal_dir={wal_dir}",
    ]
    stdout_path = logs / f"{mode}_{th}_r{repeat}.out"
    stderr_path = logs / f"{mode}_{th}_r{repeat}.err"
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=err)
    text = stdout_path.read_text(errors="replace")
    row = parse_metrics(text)
    row["repeat"] = str(repeat)
    row["exit_code"] = str(proc.returncode)
    row["stdout_log"] = str(stdout_path)
    row["stderr_log"] = str(stderr_path)
    return row


def pct(n, denom):
    try:
        n = float(n)
        denom = float(denom)
    except Exception:
        return "0.00"
    return f"{(n / denom * 100.0) if denom else 0.0:.2f}"


def write_markdown(result_path, rows):
    md = result_path.with_suffix(".md")
    by_mode_th = {}
    for mode in MODES:
        for th in THREADS:
            rs = [r for r in rows if r.get("mode") == mode and int(r.get("thread_num", "0")) == th]
            if rs:
                by_mode_th[(mode, th)] = rs
    with md.open("w") as f:
        print("# no-CC WAL durability microbench", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print(f"seconds: {SECONDS}", file=f)
        print(f"repeats: {REPEATS}", file=f)
        print(f"write_set_size: {WRITE_SET_SIZE}", file=f)
        print(f"value_size: {VALUE_SIZE}", file=f)
        print(f"group_size: {GROUP_SIZE}", file=f)
        print(f"flush_us: {FLUSH_US}", file=f)
        print(f"prealloc_adaptive: {PREALLOC_ADAPTIVE}", file=f)
        print(f"prealloc_mb: {PREALLOC_MB}", file=f)
        print("", file=f)
        print("## Throughput mean", file=f)
        print("", file=f)
        print("| mode | 1 | 2 | 4 | 8 | 16 | 32 |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|", file=f)
        for mode in MODES:
            vals = []
            for th in THREADS:
                rs = by_mode_th[(mode, th)]
                vals.append(f"{statistics.mean(float(r.get('throughput_tps', '0') or 0) for r in rs):.0f}")
            print("| " + mode + " | " + " | ".join(vals) + " |", file=f)
        print("", file=f)
        print("## Throughput stddev", file=f)
        print("", file=f)
        print("| mode | 1 | 2 | 4 | 8 | 16 | 32 |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|", file=f)
        for mode in MODES:
            vals = []
            for th in THREADS:
                xs = [float(r.get("throughput_tps", "0") or 0) for r in by_mode_th[(mode, th)]]
                vals.append(f"{statistics.stdev(xs):.0f}" if len(xs) > 1 else "0")
            print("| " + mode + " | " + " | ".join(vals) + " |", file=f)
        print("", file=f)
        print("## 32-thread detail", file=f)
        print("", file=f)
        print("| mode | tps mean | tps stddev | closed-loop avg us | sampled p50 us | sampled p99 us | fdatasync/s | commits/fdatasync | MB/s | build% | mutex% | write% | fdatasync% | prefix_wait% |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for mode in MODES:
            rs = by_mode_th[(mode, 32)]
            r = rs[0]
            denom_ns = float(r.get("actual_sec", "0") or 0) * 32 * 1_000_000_000.0
            mbps = float(r.get("log_bytes_per_sec", "0") or 0) / 1024 / 1024
            tps_vals = [float(x.get("throughput_tps", "0") or 0) for x in rs]
            def avg(key):
                return statistics.mean(float(x.get(key, "0") or 0) for x in rs)
            print(
                "| "
                + " | ".join(
                    [
                        mode,
                        f"{statistics.mean(tps_vals):.0f}",
                        f"{statistics.stdev(tps_vals):.0f}" if len(tps_vals) > 1 else "0",
                        f"{avg('closed_loop_avg_latency_us'):.1f}",
                        f"{avg('latency_p50_us'):.0f}",
                        f"{avg('latency_p99_us'):.0f}",
                        f"{avg('fdatasync_per_sec'):.1f}",
                        f"{avg('commits_per_fdatasync'):.2f}",
                        f"{mbps:.1f}",
                        pct(r.get("payload_build_ns", "0"), denom_ns),
                        pct(r.get("mutex_wait_ns", "0"), denom_ns),
                        pct(r.get("write_ns", "0"), denom_ns),
                        pct(r.get("fdatasync_ns", "0"), denom_ns),
                        pct(r.get("prefix_wait_ns", "0"), denom_ns),
                    ]
                )
                + " |",
                file=f,
            )
        print("", file=f)
        print("## Initial reading", file=f)
        print("", file=f)
        print("- `single_wal` exposes the global WAL mutex plus one fdatasync per commit.", file=f)
        print("- `single_wal_group_commit` is the fair centralized WAL group-commit baseline.", file=f)
        print("- `pwal_per_txn_fdatasync` removes the single WAL mutex, but keeps one fdatasync per commit.", file=f)
        print("- `pwal_group_commit` batches fdatasync but waits for a global durable prefix across logger shards.", file=f)
        print("- `pwal_group_commit_no_prefix` waits only for the local logger shard. This is an unsafe upper bound for general transaction workloads.", file=f)
    return md


def main():
    build()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_dir = ROOT / "results" / f"no_cc_wal_microbench_{stamp}"
    result_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(REPEATS):
        for th in THREADS:
            for mode in MODES:
                print(f"RUN repeat={repeat} {mode} threads={th}", flush=True)
                rows.append(run_case(stamp, mode, th, repeat))
    result_path = result_dir / f"no_cc_wal_microbench_{stamp}.csv"
    fieldnames = sorted(set().union(*(r.keys() for r in rows)))
    with result_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    md = write_markdown(result_path, rows)
    print(result_path)
    print(md)


if __name__ == "__main__":
    main()
