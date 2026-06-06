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

INFLIGHTS = [1, 2, 4, 8, 16, 32, 64]
SECONDS = int(os.environ.get("CSTAMP_PWAL_SECONDS", "1"))
REPEATS = int(os.environ.get("CSTAMP_PWAL_REPEATS", "1"))
THREAD_NUM = int(os.environ.get("CSTAMP_PWAL_THREAD_NUM", "32"))
LOGGER_NUM = int(os.environ.get("CSTAMP_PWAL_LOGGER_NUM", "4"))
WRITE_SET_SIZE = int(os.environ.get("CSTAMP_PWAL_WRITE_SET_SIZE", "10"))
VALUE_SIZE = int(os.environ.get("CSTAMP_PWAL_VALUE_SIZE", "32"))
GROUP_SIZE = int(os.environ.get("CSTAMP_PWAL_GROUP_SIZE", "8"))
FLUSH_US = int(os.environ.get("CSTAMP_PWAL_FLUSH_US", "100"))
DEP_PROB_PPM = int(os.environ.get("CSTAMP_PWAL_DEP_PROB_PPM", "100000"))
DEP_FANOUT = int(os.environ.get("CSTAMP_PWAL_DEP_FANOUT", "1"))
PREALLOC_MB = int(os.environ.get("CSTAMP_PWAL_PREALLOC_MB", "64"))
STRAGGLER_LOGGER = int(os.environ.get("CSTAMP_PWAL_STRAGGLER_LOGGER", "-1"))
STRAGGLER_SLEEP_US = int(os.environ.get("CSTAMP_PWAL_STRAGGLER_SLEEP_US", "0"))
STRAGGLER_EXTRA_BYTES = int(os.environ.get("CSTAMP_PWAL_STRAGGLER_EXTRA_BYTES", "0"))


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


def run_case(stamp, max_inflight, repeat):
    out_dir = ROOT / "results" / f"cstamp_pwal_inflight_sweep_{stamp}"
    wal_dir = out_dir / "wal_files"
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(EXE),
        "--mode=cstamp_pwal_async_dep_frontier",
        f"--thread_num={THREAD_NUM}",
        f"--seconds={SECONDS}",
        f"--write_set_size={WRITE_SET_SIZE}",
        f"--value_size={VALUE_SIZE}",
        f"--group_size={GROUP_SIZE}",
        f"--flush_us={FLUSH_US}",
        f"--logger_num={LOGGER_NUM}",
        f"--prealloc_mb={PREALLOC_MB}",
        f"--dep_prob_ppm={DEP_PROB_PPM}",
        f"--dep_fanout={DEP_FANOUT}",
        f"--max_inflight={max_inflight}",
        f"--straggler_logger={STRAGGLER_LOGGER}",
        f"--straggler_sleep_us={STRAGGLER_SLEEP_US}",
        f"--straggler_extra_bytes={STRAGGLER_EXTRA_BYTES}",
        f"--wal_dir={wal_dir}",
    ]
    stdout_path = logs / f"inflight{max_inflight}_r{repeat}.out"
    stderr_path = logs / f"inflight{max_inflight}_r{repeat}.err"
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=err)
    row = parse_metrics(stdout_path.read_text(errors="replace"))
    row["max_inflight_per_worker"] = str(max_inflight)
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
    width = 900
    height = 500
    left = 82
    right = 30
    top = 48
    bottom = 70
    plot_w = width - left - right
    plot_h = height - top - bottom
    ymax = max(mean(grouped[v], metric) for v in INFLIGHTS) * 1.10
    if ymax == 0:
        ymax = 1.0

    def x_pos(v):
        idx = INFLIGHTS.index(v)
        return left + plot_w * idx / (len(INFLIGHTS) - 1)

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
        pts = []
        for v in INFLIGHTS:
            px = x_pos(v)
            y = mean(grouped[v], metric)
            py = y_pos(y)
            pts.append(f"{px:.1f},{py:.1f}")
            print(f'<text x="{px:.1f}" y="{top + plot_h + 24}" text-anchor="middle" font-family="sans-serif" font-size="12">{v}</text>', file=f)
        print(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#8c564b" stroke-width="3"/>', file=f)
        for v in INFLIGHTS:
            print(f'<circle cx="{x_pos(v):.1f}" cy="{y_pos(mean(grouped[v], metric)):.1f}" r="4" fill="#8c564b"/>', file=f)
        print(f'<text x="{left + plot_w / 2:.1f}" y="{height - 20}" text-anchor="middle" font-family="sans-serif" font-size="14">max_inflight per worker</text>', file=f)
        print(f'<text x="18" y="{top + plot_h / 2:.1f}" text-anchor="middle" font-family="sans-serif" font-size="14" transform="rotate(-90 18 {top + plot_h / 2:.1f})">{y_label}</text>', file=f)
        print("</svg>", file=f)


def write_report(csv_path, rows):
    md = csv_path.with_suffix(".md")
    grouped = {}
    for r in rows:
        grouped.setdefault(int(r["max_inflight_per_worker"]), []).append(r)
    throughput_svg = csv_path.with_name(csv_path.stem + "_throughput.svg")
    p99_svg = csv_path.with_name(csv_path.stem + "_p99_latency.svg")
    write_svg(throughput_svg, grouped, "throughput_tps", "Acked throughput vs max_inflight", "acked tx/s")
    write_svg(p99_svg, grouped, "latency_p99_us", "Durable ack p99 latency vs max_inflight", "p99 us")

    with md.open("w") as f:
        print("# Cstamp-PWAL max_inflight sweep", file=f)
        print("", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---:|", file=f)
        print(f"| threads | {THREAD_NUM} |", file=f)
        print(f"| logger_num | {LOGGER_NUM} |", file=f)
        print(f"| seconds | {SECONDS} |", file=f)
        print(f"| repeats | {REPEATS} |", file=f)
        print(f"| group_size | {GROUP_SIZE} |", file=f)
        print(f"| flush_us | {FLUSH_US} |", file=f)
        print(f"| dep_prob_ppm | {DEP_PROB_PPM} |", file=f)
        print(f"| dep_fanout | {DEP_FANOUT} |", file=f)
        print(f"| straggler_logger | {STRAGGLER_LOGGER} |", file=f)
        print(f"| straggler_sleep_us | {STRAGGLER_SLEEP_US} |", file=f)
        print("", file=f)
        print("## Graphs", file=f)
        print("", file=f)
        print(f"![throughput]({throughput_svg.name})", file=f)
        print("", file=f)
        print(f"![p99 latency]({p99_svg.name})", file=f)
        print("", file=f)
        print("## Detail", file=f)
        print("", file=f)
        print("| max_inflight | ack tps | logical-acked | p50 us | p99 us | queue_wait_us/tx | worker_stall_us/tx | max_pending | max_waitlist |", file=f)
        print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|", file=f)
        for v in INFLIGHTS:
            rs = grouped[v]
            commits = max(mean(rs, "commits"), 1.0)
            print(
                "| "
                + " | ".join(
                    [
                        str(v),
                        f"{mean(rs, 'throughput_tps'):.0f}",
                        f"{mean(rs, 'logical_minus_acked'):.0f}",
                        f"{mean(rs, 'latency_p50_us'):.0f}",
                        f"{mean(rs, 'latency_p99_us'):.0f}",
                        f"{mean(rs, 'committer_queue_wait_ns') / commits / 1000:.1f}",
                        f"{mean(rs, 'worker_stall_ns') / commits / 1000:.1f}",
                        f"{mean(rs, 'max_pending_len'):.0f}",
                        f"{mean(rs, 'max_waitlist_len'):.0f}",
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
    out_dir = ROOT / "results" / f"cstamp_pwal_inflight_sweep_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(REPEATS):
        for max_inflight in INFLIGHTS:
            print(f"RUN repeat={repeat} max_inflight={max_inflight}", flush=True)
            rows.append(run_case(stamp, max_inflight, repeat))
    csv_path = out_dir / f"cstamp_pwal_inflight_sweep_{stamp}.csv"
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
