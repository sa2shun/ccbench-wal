#!/usr/bin/env python3
import csv
import sys
from pathlib import Path

FIELDS = [
    ("payload_build_ns_pct", "payload", "#6a9fb5"),
    ("mutex_wait_ns_pct", "mutex wait", "#d95f02"),
    ("write_ns_pct", "write", "#7570b3"),
    ("fdatasync_ns_pct", "fdatasync", "#1b9e77"),
    ("notify_wait_ns_pct", "notify wait", "#e7298a"),
]
ROWS = [
    ("normal_ycsb", "Normal ERMIA YCSB"),
    ("abort0_ycsb", "Abort-0 ERMIA YCSB"),
    ("wal_framework", "No-CC WAL Framework"),
]
THREADS = [1, 16, 32]


def rows(path):
    lines = path.read_text().splitlines()
    return list(csv.DictReader(lines[lines.index("===== SUMMARY_CSV =====") + 1:]))


def modes_for(experiment):
    if experiment == "wal_framework":
        return ["wal", "pwal"]
    return ["ermia_wal", "ermia_pwal"]


def short_mode(mode):
    return "WAL" if mode in ("wal", "ermia_wal") else "P-WAL"


def draw_cell(svg, data, experiment, thread, x, y, w, h):
    modes = modes_for(experiment)
    subset = {r["mode"]: r for r in data if r["experiment"] == experiment and int(r["thread_num"]) == thread}
    base = y + h - 34
    plot_h = h - 74
    bar_w = 38
    gap = 28
    start_x = x + 54
    svg.append(f'<text x="{x+6}" y="{y+16}" font-size="12" font-weight="700" fill="#152534">{thread} threads</text>')
    for i, mode in enumerate(modes):
        r = subset[mode]
        bx = start_x + i * (bar_w + gap)
        top = base
        for field, _, color in FIELDS:
            pct = float(r[field])
            bh = plot_h * pct / 100.0
            top -= bh
            svg.append(f'<rect x="{bx}" y="{top:.1f}" width="{bar_w}" height="{bh:.1f}" fill="{color}"/>')
        svg.append(f'<text x="{bx+bar_w/2}" y="{base+13}" text-anchor="middle" font-size="9" fill="#334455">{short_mode(mode)}</text>')
        svg.append(f'<text x="{bx+bar_w/2}" y="{base+26}" text-anchor="middle" font-size="8" fill="#586879">{float(r["throughput_tps"]):.0f}</text>')
    for j in [0, 50, 100]:
        yy = base - plot_h * j / 100.0
        svg.append(f'<line x1="{x+32}" y1="{yy:.1f}" x2="{x+w-8}" y2="{yy:.1f}" stroke="#edf1f4"/>')
        svg.append(f'<text x="{x+27}" y="{yy+3:.1f}" text-anchor="end" font-size="8" fill="#8a98a8">{j}</text>')


def main():
    path = Path(sys.argv[1])
    data = rows(path)
    out = path.with_name(path.stem + "_dashboard.svg")
    width = 1440
    height = 920
    left = 36
    top = 150
    row_h = 230
    col_w = 405
    gap_x = 34
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#f7f9fb"/>',
        '<text x="36" y="42" font-size="28" font-weight="800" fill="#152534">WAL Cost Breakdown</text>',
        '<text x="36" y="68" font-size="13" fill="#586879">Normal YCSB, abort-0 YCSB, and no-CC WAL framework. Each cell shows WAL vs P-WAL accounted time.</text>',
    ]
    lx = 36
    for _, label, color in FIELDS:
        svg.append(f'<rect x="{lx}" y="94" width="13" height="13" fill="{color}"/>')
        svg.append(f'<text x="{lx+19}" y="106" font-size="12" fill="#334455">{label}</text>')
        lx += 156
    for row_i, (experiment, title) in enumerate(ROWS):
        y = top + row_i * row_h
        svg.append(f'<text x="{left}" y="{y-14}" font-size="18" font-weight="800" fill="#152534">{title}</text>')
        for col_i, thread in enumerate(THREADS):
            x = left + col_i * (col_w + gap_x)
            svg.append(f'<rect x="{x}" y="{y}" width="{col_w}" height="{row_h-38}" fill="#ffffff" stroke="#e1e6eb" rx="6"/>')
            draw_cell(svg, data, experiment, thread, x + 8, y + 12, col_w - 16, row_h - 62)
    svg.append('<text x="36" y="895" font-size="11" fill="#586879">Numbers under bars are throughput[tps]. Cost percentages are accumulated thread time, so mutex wait can dominate shared WAL under contention.</text>')
    svg.append('</svg>')
    out.write_text("\n".join(svg))
    print(out)


if __name__ == "__main__":
    main()
