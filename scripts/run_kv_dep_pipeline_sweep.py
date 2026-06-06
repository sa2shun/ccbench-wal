#!/usr/bin/env python3
import csv
import os
import re
import subprocess
import statistics
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "build" / "kv_dep_wal_microbench.exe"
SRC = ROOT / "tools" / "kv_dep_wal_microbench.cc"

THREADS = [1, 2, 4, 8, 16, 32]
MODES = ["pwal_group_dep_frontier", "async_dep_frontier_lsn", "async_dep_frontier_cstamp"]
COLORS = {
    "pwal_group_dep_frontier": "#ff7f0e",
    "async_dep_frontier_lsn": "#17becf",
    "async_dep_frontier_cstamp": "#8c564b",
}

SECONDS = int(os.environ.get("KV_DEP_SECONDS", "1"))
REPEATS = int(os.environ.get("KV_DEP_REPEATS", "1"))
LOGGER_NUM = int(os.environ.get("KV_DEP_LOGGER_NUM", "4"))
KEYS_PER_LOGGER = int(os.environ.get("KV_DEP_KEYS_PER_LOGGER", "1024"))
REMOTE_READ_PROB_PPM = int(os.environ.get("KV_DEP_REMOTE_READ_PROB_PPM", "100000"))
HOT_PROB_PPM = int(os.environ.get("KV_DEP_HOT_PROB_PPM", "0"))
GROUP_SIZE = int(os.environ.get("KV_DEP_GROUP_SIZE", "8"))
FLUSH_US = int(os.environ.get("KV_DEP_FLUSH_US", "100"))
MAX_INFLIGHT = int(os.environ.get("KV_DEP_MAX_INFLIGHT", "1024"))
PREALLOC_MB = int(os.environ.get("KV_DEP_PREALLOC_MB", "64"))
SKIP_FDATASYNC = int(os.environ.get("KV_DEP_SKIP_FDATASYNC", "0"))


def build():
    EXE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["g++", "-O3", "-std=c++17", "-pthread", str(SRC), "-o", str(EXE)], cwd=ROOT, check=True)


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            row[m.group(1)] = m.group(2)
    return row


def run_case(stamp, mode, thread_num, repeat):
    out_dir = ROOT / "results" / f"kv_dep_pipeline_sweep_{stamp}"
    wal_dir = out_dir / "wal_files"
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logger_num = min(LOGGER_NUM, thread_num)
    cmd = [
        str(EXE),
        f"--mode={mode}",
        f"--thread_num={thread_num}",
        f"--logger_num={logger_num}",
        f"--seconds={SECONDS}",
        f"--keys_per_logger={KEYS_PER_LOGGER}",
        f"--remote_read_prob_ppm={REMOTE_READ_PROB_PPM}",
        f"--hot_prob_ppm={HOT_PROB_PPM}",
        f"--group_size={GROUP_SIZE}",
        f"--flush_us={FLUSH_US}",
        f"--max_inflight={MAX_INFLIGHT}",
        f"--prealloc_mb={PREALLOC_MB}",
        f"--skip_fdatasync={SKIP_FDATASYNC}",
        f"--wal_dir={wal_dir}",
    ]
    stdout_path = logs / f"{mode}_t{thread_num}_r{repeat}.out"
    stderr_path = logs / f"{mode}_t{thread_num}_r{repeat}.err"
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=err)
    row = parse_metrics(stdout_path.read_text(errors="replace"))
    row["mode"] = mode
    row["thread_num"] = str(thread_num)
    row["repeat"] = str(repeat)
    row["exit_code"] = str(proc.returncode)
    row["stdout_log"] = str(stdout_path)
    row["stderr_log"] = str(stderr_path)
    return row


def fnum(row, key):
    try:
        return float(row.get(key, "0") or 0)
    except Exception:
        return 0.0


def mean(rows, key):
    return statistics.mean(fnum(r, key) for r in rows)


def write_svg(path, grouped):
    width = 940
    height = 520
    left = 84
    right = 220
    top = 48
    bottom = 72
    plot_w = width - left - right
    plot_h = height - top - bottom
    ymax = max(mean(grouped[(m, t)], "throughput_tps") for m in MODES for t in THREADS) * 1.10
    if ymax == 0:
        ymax = 1.0

    def x_pos(t):
        return left + plot_w * THREADS.index(t) / (len(THREADS) - 1)

    def y_pos(v):
        return top + plot_h - (v / ymax) * plot_h

    with path.open("w") as f:
        print('<?xml version="1.0" encoding="UTF-8"?>', file=f)
        print(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', file=f)
        print('<rect width="100%" height="100%" fill="white"/>', file=f)
        print(f'<text x="{left}" y="28" font-family="sans-serif" font-size="20" font-weight="700">KV pipeline effect</text>', file=f)
        print(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#333"/>', file=f)
        print(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#333"/>', file=f)
        for i in range(6):
            y = ymax * i / 5
            py = y_pos(y)
            print(f'<line x1="{left}" y1="{py:.1f}" x2="{left + plot_w}" y2="{py:.1f}" stroke="#e6e6e6"/>', file=f)
            print(f'<text x="{left - 10}" y="{py + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="12">{y:.0f}</text>', file=f)
        for t in THREADS:
            print(f'<text x="{x_pos(t):.1f}" y="{top + plot_h + 24}" text-anchor="middle" font-family="sans-serif" font-size="12">{t}</text>', file=f)
        for mode in MODES:
            pts = " ".join(f"{x_pos(t):.1f},{y_pos(mean(grouped[(mode, t)], 'throughput_tps')):.1f}" for t in THREADS)
            print(f'<polyline points="{pts}" fill="none" stroke="{COLORS[mode]}" stroke-width="3"/>', file=f)
            for t in THREADS:
                print(f'<circle cx="{x_pos(t):.1f}" cy="{y_pos(mean(grouped[(mode, t)], "throughput_tps")):.1f}" r="4" fill="{COLORS[mode]}"/>', file=f)
        lx = left + plot_w + 24
        ly = top + 20
        for i, mode in enumerate(MODES):
            y = ly + i * 24
            print(f'<rect x="{lx}" y="{y - 10}" width="14" height="14" fill="{COLORS[mode]}"/>', file=f)
            print(f'<text x="{lx + 22}" y="{y + 2}" font-family="sans-serif" font-size="12">{mode}</text>', file=f)
        print(f'<text x="{left + plot_w / 2:.1f}" y="{height - 20}" text-anchor="middle" font-family="sans-serif" font-size="14">threads</text>', file=f)
        print("</svg>", file=f)


def write_report(csv_path, rows):
    md = csv_path.with_suffix(".md")
    grouped = {}
    for r in rows:
        grouped.setdefault((r["mode"], int(r["thread_num"])), []).append(r)
    throughput_svg = csv_path.with_name(csv_path.stem + "_throughput.svg")
    write_svg(throughput_svg, grouped)

    with md.open("w") as f:
        print("# KV dependency pipeline sweep", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---:|", file=f)
        print(f"| seconds | {SECONDS} |", file=f)
        print(f"| repeats | {REPEATS} |", file=f)
        print(f"| logger_num max | {LOGGER_NUM} |", file=f)
        print(f"| remote_read_prob_ppm | {REMOTE_READ_PROB_PPM} |", file=f)
        print(f"| hot_prob_ppm | {HOT_PROB_PPM} |", file=f)
        print(f"| skip_fdatasync | {SKIP_FDATASYNC} |", file=f)
        print("", file=f)
        print(f"![throughput]({throughput_svg.name})", file=f)
        print("", file=f)
        print("## Throughput", file=f)
        print("", file=f)
        print("| mode | 1 | 2 | 4 | 8 | 16 | 32 |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|", file=f)
        for mode in MODES:
            vals = [f"{mean(grouped[(mode, t)], 'throughput_tps'):.0f}" for t in THREADS]
            print("| " + mode + " | " + " | ".join(vals) + " |", file=f)
        print("", file=f)
        print("## Detail", file=f)
        print("", file=f)
        print("| mode | threads | p99 us | worker_wait_us/tx | queue_wait_us/tx | atomic/tx | max_pending |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|", file=f)
        for t in THREADS:
            for mode in MODES:
                rs = grouped[(mode, t)]
                commits = max(mean(rs, "commits"), 1.0)
                print(
                    "| "
                    + " | ".join(
                        [
                            mode,
                            str(t),
                            f"{mean(rs, 'latency_p99_us'):.0f}",
                            f"{mean(rs, 'worker_wait_ns') / commits / 1000:.1f}",
                            f"{mean(rs, 'committer_queue_wait_ns') / commits / 1000:.1f}",
                            f"{mean(rs, 'global_atomic_count') / commits:.2f}",
                            f"{mean(rs, 'max_pending_len'):.0f}",
                        ]
                    )
                    + " |",
                    file=f,
                )
        print("", file=f)
        print(f"Raw CSV: `{csv_path}`", file=f)
    return md


def main():
    build()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "results" / f"kv_dep_pipeline_sweep_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(REPEATS):
        for t in THREADS:
            for mode in MODES:
                print(f"RUN repeat={repeat} mode={mode} threads={t}", flush=True)
                rows.append(run_case(stamp, mode, t, repeat))
    csv_path = out_dir / f"kv_dep_pipeline_sweep_{stamp}.csv"
    fieldnames = sorted(set().union(*(r.keys() for r in rows)))
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    md = write_report(csv_path, rows)
    print(csv_path)
    print(md)


if __name__ == "__main__":
    main()
