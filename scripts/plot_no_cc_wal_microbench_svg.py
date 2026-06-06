#!/usr/bin/env python3
import csv
import math
import statistics
import sys
from pathlib import Path

SERIES_COLORS = {
    "no_durability": "#555555",
    "single_wal": "#d62728",
    "single_wal_group_commit": "#9467bd",
    "pwal_per_txn_fdatasync": "#ff7f0e",
    "pwal_group_commit": "#1f77b4",
    "pwal_group_commit_no_prefix": "#2ca02c",
    "pwal_group_dep_frontier": "#17becf",
    "async_global_lsn_prefix": "#d62728",
    "async_global_prefix_lsn": "#1f77b4",
    "async_local_only_lsn": "#2ca02c",
    "async_dep_frontier_lsn": "#17becf",
    "async_dep_frontier_cstamp": "#8c564b",
}

PLOTS = [
    ("throughput_tps", "Throughput", "tx/s", True),
    ("closed_loop_avg_latency_us", "closed-loop average latency", "us", True),
    ("latency_p99_us", "p99 latency", "us", True),
    ("fdatasync_per_sec", "fdatasync rate", "fdatasync/s", True),
    ("commits_per_fdatasync", "commits per fdatasync", "commits/fdatasync", False),
]


def num(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def nice_max(v):
    if v <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(v))
    return math.ceil(v / p) * p


def draw_plot(rows, metric, title, ylabel, logy):
    modes = list(SERIES_COLORS)
    threads = sorted({int(r["thread_num"]) for r in rows})
    grouped = {}
    for r in rows:
        grouped.setdefault((r["mode"], int(r["thread_num"])), []).append(r)
    by = {}
    for key, rs in grouped.items():
        merged = dict(rs[0])
        for col in PLOTS:
            values = [num(r.get(col[0], 0)) for r in rs]
            merged[col[0]] = str(statistics.mean(values))
        by[key] = merged
    width, height = 980, 520
    left, right, top, bottom = 86, 245, 46, 70
    plot_w = width - left - right
    plot_h = height - top - bottom
    vals = [max(num(by.get((m, t), {}).get(metric, 0)), 0.0) for m in modes for t in threads]
    if logy:
        positive = [v for v in vals if v > 0]
        ymin = max(min(positive) / 2 if positive else 1, 1e-3)
        ymax = max(positive) * 1.5 if positive else 1
        def y(v):
            v = max(v, ymin)
            return top + plot_h * (1 - (math.log10(v) - math.log10(ymin)) / (math.log10(ymax) - math.log10(ymin)))
        yticks = []
        p = math.floor(math.log10(ymin))
        while 10 ** p <= ymax:
            yticks.append(10 ** p)
            p += 1
    else:
        ymin, ymax = 0, nice_max(max(vals))
        def y(v):
            return top + plot_h * (1 - (v - ymin) / (ymax - ymin))
        yticks = [ymax * i / 5 for i in range(6)]
    def x(t):
        i = threads.index(t)
        return left + plot_w * i / (len(threads) - 1)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="28" font-family="sans-serif" font-size="20" font-weight="700">{title}</text>',
        f'<text x="18" y="{top + plot_h / 2}" transform="rotate(-90 18 {top + plot_h / 2})" font-family="sans-serif" font-size="13">{ylabel}</text>',
    ]
    for tick in yticks:
        yy = y(tick)
        parts.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#e6e6e6"/>')
        label = f"{tick:.0f}" if tick >= 1 else f"{tick:.3g}"
        parts.append(f'<text x="{left - 10}" y="{yy + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="11">{label}</text>')
    parts.append(f'<line x1="{left}" x2="{left}" y1="{top}" y2="{top + plot_h}" stroke="#333"/>')
    parts.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{top + plot_h}" y2="{top + plot_h}" stroke="#333"/>')
    for t in threads:
        xx = x(t)
        parts.append(f'<text x="{xx}" y="{top + plot_h + 24}" text-anchor="middle" font-family="sans-serif" font-size="12">{t}</text>')
    parts.append(f'<text x="{left + plot_w / 2}" y="{height - 18}" text-anchor="middle" font-family="sans-serif" font-size="13">threads</text>')
    for mode in modes:
        pts = []
        for t in threads:
            r = by.get((mode, t))
            if r:
                pts.append((x(t), y(num(r.get(metric, 0)))))
        if not pts:
            continue
        points = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        color = SERIES_COLORS[mode]
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for px, py in pts:
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{color}"/>')
    ly = top + 20
    for mode in modes:
        color = SERIES_COLORS[mode]
        parts.append(f'<rect x="{left + plot_w + 28}" y="{ly - 10}" width="14" height="14" fill="{color}"/>')
        parts.append(f'<text x="{left + plot_w + 50}" y="{ly + 2}" font-family="sans-serif" font-size="12">{mode}</text>')
        ly += 24
    parts.append("</svg>")
    return "\n".join(parts)


def main():
    if len(sys.argv) != 2:
        print("usage: plot_no_cc_wal_microbench_svg.py result.csv", file=sys.stderr)
        sys.exit(2)
    csv_path = Path(sys.argv[1])
    rows = list(csv.DictReader(csv_path.open()))
    for metric, title, ylabel, logy in PLOTS:
        out = csv_path.with_name(csv_path.stem + f"_{metric}.svg")
        out.write_text(draw_plot(rows, metric, title, ylabel, logy))
        print(out)


if __name__ == "__main__":
    main()
