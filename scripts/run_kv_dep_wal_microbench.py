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

MODES = [
    "async_global_lsn_prefix",
    "async_local_only_lsn",
    "async_dep_frontier_lsn",
    "async_dep_frontier_cstamp",
]

COLORS = {
    "async_global_lsn_prefix": "#1f77b4",
    "async_local_only_lsn": "#2ca02c",
    "async_dep_frontier_lsn": "#17becf",
    "async_dep_frontier_cstamp": "#8c564b",
}

REMOTE_PROBS = [0, 10_000, 50_000, 100_000, 500_000, 1_000_000]
SECONDS = int(os.environ.get("KV_DEP_SECONDS", "1"))
REPEATS = int(os.environ.get("KV_DEP_REPEATS", "1"))
THREAD_NUM = int(os.environ.get("KV_DEP_THREAD_NUM", "8"))
LOGGER_NUM = int(os.environ.get("KV_DEP_LOGGER_NUM", "4"))
KEYS_PER_LOGGER = int(os.environ.get("KV_DEP_KEYS_PER_LOGGER", "1024"))
HOT_PROB_PPM = int(os.environ.get("KV_DEP_HOT_PROB_PPM", "0"))
GROUP_SIZE = int(os.environ.get("KV_DEP_GROUP_SIZE", "8"))
FLUSH_US = int(os.environ.get("KV_DEP_FLUSH_US", "100"))
MAX_INFLIGHT = int(os.environ.get("KV_DEP_MAX_INFLIGHT", "1024"))
PREALLOC_MB = int(os.environ.get("KV_DEP_PREALLOC_MB", "64"))
SKIP_FDATASYNC = int(os.environ.get("KV_DEP_SKIP_FDATASYNC", "0"))


def build():
    EXE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["g++", "-O3", "-std=c++17", "-pthread", str(SRC), "-o", str(EXE)],
        cwd=ROOT,
        check=True,
    )


def parse_metrics(text):
    row = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            row[m.group(1)] = m.group(2)
    return row


def run_case(stamp, mode, remote_prob, repeat):
    out_dir = ROOT / "results" / f"kv_dep_wal_microbench_{stamp}"
    wal_dir = out_dir / "wal_files"
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(EXE),
        f"--mode={mode}",
        f"--thread_num={THREAD_NUM}",
        f"--logger_num={LOGGER_NUM}",
        f"--seconds={SECONDS}",
        f"--keys_per_logger={KEYS_PER_LOGGER}",
        f"--remote_read_prob_ppm={remote_prob}",
        f"--hot_prob_ppm={HOT_PROB_PPM}",
        f"--group_size={GROUP_SIZE}",
        f"--flush_us={FLUSH_US}",
        f"--max_inflight={MAX_INFLIGHT}",
        f"--prealloc_mb={PREALLOC_MB}",
        f"--skip_fdatasync={SKIP_FDATASYNC}",
        f"--wal_dir={wal_dir}",
    ]
    stdout_path = logs / f"{mode}_remote{remote_prob}_r{repeat}.out"
    stderr_path = logs / f"{mode}_remote{remote_prob}_r{repeat}.err"
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=err)
    row = parse_metrics(stdout_path.read_text(errors="replace"))
    row["mode"] = mode
    row["remote_read_prob_ppm"] = str(remote_prob)
    row["remote_read_prob"] = f"{remote_prob / 1_000_000:.3f}"
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


def write_svg(path, grouped, metric, title, y_label):
    width = 960
    height = 520
    left = 84
    right = 34
    top = 48
    bottom = 78
    plot_w = width - left - right
    plot_h = height - top - bottom
    xs = [p / 1_000_000 for p in REMOTE_PROBS]
    ymax = 0.0
    for mode in MODES:
      ymax = max(ymax, max(mean(grouped[(mode, p)], metric) for p in REMOTE_PROBS))
    ymax = ymax * 1.10 if ymax else 1.0

    def x_pos(x):
        return left + x * plot_w

    def y_pos(y):
        return top + plot_h - (y / ymax) * plot_h

    with path.open("w") as f:
        print('<?xml version="1.0" encoding="UTF-8"?>', file=f)
        print(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', file=f)
        print('<rect width="100%" height="100%" fill="white"/>', file=f)
        print(f'<text x="{left}" y="28" font-family="sans-serif" font-size="20" font-weight="700">{title}</text>', file=f)
        print(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#333"/>', file=f)
        print(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#333"/>', file=f)
        for i in range(6):
            y = ymax * i / 5
            py = y_pos(y)
            print(f'<line x1="{left}" y1="{py:.1f}" x2="{left + plot_w}" y2="{py:.1f}" stroke="#e6e6e6"/>', file=f)
            print(f'<text x="{left - 10}" y="{py + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="12">{y:.0f}</text>', file=f)
        for x in xs:
            px = x_pos(x)
            print(f'<line x1="{px:.1f}" y1="{top + plot_h}" x2="{px:.1f}" y2="{top + plot_h + 5}" stroke="#333"/>', file=f)
            print(f'<text x="{px:.1f}" y="{top + plot_h + 24}" text-anchor="middle" font-family="sans-serif" font-size="12">{x:.2f}</text>', file=f)
        print(f'<text x="{left + plot_w / 2:.1f}" y="{height - 22}" text-anchor="middle" font-family="sans-serif" font-size="14">remote read probability</text>', file=f)
        print(f'<text x="18" y="{top + plot_h / 2:.1f}" text-anchor="middle" font-family="sans-serif" font-size="14" transform="rotate(-90 18 {top + plot_h / 2:.1f})">{y_label}</text>', file=f)
        for mode in MODES:
            color = COLORS[mode]
            pts = " ".join(
                f"{x_pos(p / 1_000_000):.1f},{y_pos(mean(grouped[(mode, p)], metric)):.1f}"
                for p in REMOTE_PROBS
            )
            print(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="3"/>', file=f)
            for p in REMOTE_PROBS:
                print(f'<circle cx="{x_pos(p / 1_000_000):.1f}" cy="{y_pos(mean(grouped[(mode, p)], metric)):.1f}" r="4" fill="{color}"/>', file=f)
        lx = left + 14
        ly = top + 14
        for i, mode in enumerate(MODES):
            y = ly + i * 22
            print(f'<rect x="{lx}" y="{y - 10}" width="14" height="14" fill="{COLORS[mode]}"/>', file=f)
            print(f'<text x="{lx + 22}" y="{y + 2}" font-family="sans-serif" font-size="13">{mode}</text>', file=f)
        print("</svg>", file=f)


def write_report(csv_path, rows):
    md = csv_path.with_suffix(".md")
    grouped = {}
    for r in rows:
        grouped.setdefault((r["mode"], int(r["remote_read_prob_ppm"])), []).append(r)
    throughput_svg = csv_path.with_name(csv_path.stem + "_throughput.svg")
    p99_svg = csv_path.with_name(csv_path.stem + "_p99_latency.svg")
    write_svg(throughput_svg, grouped, "throughput_tps", "KV dependency throughput", "acked tx/s")
    write_svg(p99_svg, grouped, "latency_p99_us", "KV dependency p99 durable ack latency", "p99 us")

    with md.open("w") as f:
        print("# KV dependency WAL microbench", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---:|", file=f)
        print(f"| threads | {THREAD_NUM} |", file=f)
        print(f"| logger_num | {LOGGER_NUM} |", file=f)
        print(f"| seconds | {SECONDS} |", file=f)
        print(f"| repeats | {REPEATS} |", file=f)
        print(f"| keys_per_logger | {KEYS_PER_LOGGER} |", file=f)
        print(f"| hot_prob_ppm | {HOT_PROB_PPM} |", file=f)
        print(f"| group_size | {GROUP_SIZE} |", file=f)
        print(f"| flush_us | {FLUSH_US} |", file=f)
        print(f"| max_inflight | {MAX_INFLIGHT} |", file=f)
        print(f"| skip_fdatasync | {SKIP_FDATASYNC} |", file=f)
        print("", file=f)
        print("## Graphs", file=f)
        print("", file=f)
        print(f"![throughput]({throughput_svg.name})", file=f)
        print("", file=f)
        print(f"![p99 latency]({p99_svg.name})", file=f)
        print("", file=f)
        print("## Throughput", file=f)
        print("", file=f)
        print("| mode | remote=0 | remote=0.01 | remote=0.05 | remote=0.10 | remote=0.50 | remote=1.00 |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|", file=f)
        for mode in MODES:
            vals = [f"{mean(grouped[(mode, p)], 'throughput_tps'):.0f}" for p in REMOTE_PROBS]
            print("| " + mode + " | " + " | ".join(vals) + " |", file=f)
        print("", file=f)
        print("## Detail", file=f)
        print("", file=f)
        print("| mode | remote_prob | ack tps | p50 us | p99 us | remote_reads/tx | queue_wait_us/tx | worker_wait_us/tx | atomic/tx | max_pending |", file=f)
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for p in REMOTE_PROBS:
            for mode in MODES:
                rs = grouped[(mode, p)]
                commits = max(mean(rs, "commits"), 1.0)
                print(
                    "| "
                    + " | ".join(
                        [
                            mode,
                            f"{p / 1_000_000:.2f}",
                            f"{mean(rs, 'throughput_tps'):.0f}",
                            f"{mean(rs, 'latency_p50_us'):.0f}",
                            f"{mean(rs, 'latency_p99_us'):.0f}",
                            f"{mean(rs, 'remote_reads') / commits:.3f}",
                            f"{mean(rs, 'committer_queue_wait_ns') / commits / 1000:.1f}",
                            f"{mean(rs, 'worker_wait_ns') / commits / 1000:.1f}",
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
    out_dir = ROOT / "results" / f"kv_dep_wal_microbench_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(REPEATS):
        for remote_prob in REMOTE_PROBS:
            for mode in MODES:
                print(f"RUN repeat={repeat} mode={mode} remote_prob={remote_prob}", flush=True)
                rows.append(run_case(stamp, mode, remote_prob, repeat))
    csv_path = out_dir / f"kv_dep_wal_microbench_{stamp}.csv"
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
