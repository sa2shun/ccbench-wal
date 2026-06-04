#!/usr/bin/env python3
import csv
import sys
from pathlib import Path

THREADS = [1, 2, 4, 8, 16, 32]
EXPERIMENTS = [("normal_ycsb", "Normal YCSB"), ("abort0_ycsb", "Abort-0 YCSB")]
MODES = [("ermia_wal", "ERMIA+WAL", "#2f6f9f"), ("ermia_pwal", "ERMIA+P-WAL", "#d97b32")]
TOP_STACKS = [
    ("group_ermia_other_ns_pct", "ERMIA other", "#4c78a8"),
    ("group_ssn_ns_pct", "SSN", "#f58518"),
    ("group_wal_ns_pct", "WAL", "#54a24b"),
    ("group_unaccounted_ns_pct", "unaccounted", "#d8dee6"),
]
WAL_STACKS = [
    ("wal_payload_build_ns_pct", "payload build", "#6a9fb5"),
    ("wal_mutex_wait_ns_pct", "mutex wait", "#d95f02"),
    ("wal_write_ns_pct", "write", "#7570b3"),
    ("wal_fdatasync_ns_pct", "fdatasync", "#1b9e77"),
    ("wal_notify_wait_ns_pct", "notify wait", "#e7298a"),
]


def load(path):
    lines = path.read_text().splitlines()
    return list(csv.DictReader(lines[lines.index("===== SUMMARY_CSV =====") + 1:]))


def row_for(data, exp, mode, th):
    return next(r for r in data if r["experiment"] == exp and r["mode"] == mode and int(r["thread_num"]) == th)


def num(row, key):
    try:
        return float(row.get(key, 0) or 0)
    except Exception:
        return 0.0


def text(svg, x, y, body, size=12, weight=400, fill="#17212b", anchor="start"):
    svg.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{body}</text>')


def throughput_panel(svg, data, exp, title, x, y, w, h):
    text(svg, x, y, title, 20, 800)
    vals = []
    for mode, _, _ in MODES:
        vals += [num(row_for(data, exp, mode, th), "throughput_tps") for th in THREADS]
    max_v = max(vals) * 1.12 if vals else 1
    plot_x = x + 58
    plot_y = y + 28
    plot_w = w - 78
    plot_h = h - 70
    for tick in [0, 0.25, 0.5, 0.75, 1.0]:
        yy = plot_y + plot_h - plot_h * tick
        svg.append(f'<line x1="{plot_x}" y1="{yy:.1f}" x2="{plot_x+plot_w}" y2="{yy:.1f}" stroke="#e9eef3"/>')
        text(svg, plot_x-8, yy+4, f'{max_v*tick/1000:.0f}k', 10, 400, "#687789", "end")
    svg.append(f'<line x1="{plot_x}" y1="{plot_y+plot_h}" x2="{plot_x+plot_w}" y2="{plot_y+plot_h}" stroke="#b8c2cc"/>')
    svg.append(f'<line x1="{plot_x}" y1="{plot_y}" x2="{plot_x}" y2="{plot_y+plot_h}" stroke="#b8c2cc"/>')
    x_positions = [plot_x + plot_w * i / (len(THREADS)-1) for i in range(len(THREADS))]
    for xx in x_positions:
        svg.append(f'<line x1="{xx:.1f}" y1="{plot_y}" x2="{xx:.1f}" y2="{plot_y+plot_h}" stroke="#f1f4f7"/>')
    series_points = []
    for mode, label, color in MODES:
        pts = []
        for xx, th in zip(x_positions, THREADS):
            val = num(row_for(data, exp, mode, th), "throughput_tps")
            yy = plot_y + plot_h - plot_h * val / max_v
            pts.append((xx, yy, val, th))
        path = ' '.join(('M' if i == 0 else 'L') + f' {px:.1f} {py:.1f}' for i, (px, py, _, _) in enumerate(pts))
        svg.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
        series_points.append((pts, color))
    for pts, color in series_points:
        for xx, yy, val, th in pts:
            svg.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="5.2" fill="#ffffff" stroke="{color}" stroke-width="2.4"/>')
    for i, th in enumerate(THREADS):
        xx = x_positions[i]
        text(svg, xx, plot_y + plot_h + 20, str(th), 10, 400, "#536171", "middle")
    text(svg, plot_x + plot_w/2, plot_y + plot_h + 40, "threads", 11, 400, "#536171", "middle")
    lx = x + w - 240
    for i, (_, label, color) in enumerate(MODES):
        svg.append(f'<line x1="{lx}" y1="{y+4+i*18}" x2="{lx+24}" y2="{y+4+i*18}" stroke="{color}" stroke-width="3"/>')
        text(svg, lx+32, y+8+i*18, label, 11, 600, "#334455")


def hbar(svg, x, y, w, label, values, stacks):
    text(svg, x, y+14, label, 12, 700)
    bx = x + 125
    by = y
    bw = w - 210
    bh = 24
    cur = bx
    for key, name, color in stacks:
        pct = max(0, min(100, values.get(key, 0.0)))
        seg = bw * pct / 100.0
        svg.append(f'<rect x="{cur:.1f}" y="{by}" width="{seg:.1f}" height="{bh}" fill="{color}"/>')
        if seg > 36 and pct >= 6:
            text(svg, cur + seg/2, by+16, f'{pct:.0f}%', 10, 700, "white", "middle")
        cur += seg
    svg.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" fill="none" stroke="#cbd4dd"/>')


def stacked_panel(svg, data, exp, title, x, y, w, h, stacks, prefix):
    text(svg, x, y, title, 20, 800)
    y0 = y + 36
    for i, (mode, label, _) in enumerate(MODES):
        r = row_for(data, exp, mode, 32)
        vals = {key: num(r, key) for key, _, _ in stacks}
        tps = num(r, "throughput_tps")
        abort = num(r, "abort_rate")
        hbar(svg, x, y0 + i*45, w, label, vals, stacks)
        text(svg, x+w-78, y0+16+i*45, f'{tps:.0f} tps', 11, 700, "#17212b")
        text(svg, x+w-78, y0+31+i*45, f'abort {abort:.4f}', 9, 400, "#687789")
    lx = x
    ly = y + h - 18
    for _, label, color in stacks:
        svg.append(f'<rect x="{lx}" y="{ly-10}" width="11" height="11" fill="{color}"/>')
        text(svg, lx+17, ly, label, 10, 400, "#536171")
        lx += max(108, len(label)*7 + 30)


def main():
    path = Path(sys.argv[1])
    data = load(path)
    out = path.with_name(path.stem + "_simple.svg")
    width, height = 1320, 960
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#f7f9fb"/>',
    ]
    text(svg, 34, 42, "ERMIA+WAL vs ERMIA+P-WAL: Simple Breakdown", 28, 800)
    text(svg, 34, 68, "上段: throughputの伸び。中段: 32 threadで全体時間を100%にした内訳。下段: そのWAL部分の内訳。", 13, 400, "#52606d")
    # Cards
    for x, y, w, h in [(30,100,620,300),(680,100,620,300),(30,450,620,185),(680,450,620,185),(30,690,620,185),(680,690,620,185)]:
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#ffffff" stroke="#dfe6ee" rx="6"/>')
    throughput_panel(svg, data, "normal_ycsb", "1. Throughput: Normal YCSB", 55, 135, 570, 235)
    throughput_panel(svg, data, "abort0_ycsb", "1. Throughput: Abort-0 YCSB", 705, 135, 570, 235)
    stacked_panel(svg, data, "normal_ycsb", "2. Total Time Breakdown at 32 Threads: Normal", 55, 485, 570, 120, TOP_STACKS, "")
    stacked_panel(svg, data, "abort0_ycsb", "2. Total Time Breakdown at 32 Threads: Abort-0", 705, 485, 570, 120, TOP_STACKS, "")
    stacked_panel(svg, data, "normal_ycsb", "3. WAL Internal Breakdown at 32 Threads: Normal", 55, 725, 570, 120, WAL_STACKS, "wal")
    stacked_panel(svg, data, "abort0_ycsb", "3. WAL Internal Breakdown at 32 Threads: Abort-0", 705, 725, 570, 120, WAL_STACKS, "wal")
    text(svg, 34, 925, "読み方: shared WALはmutex waitが支配的。P-WALはmutex waitをほぼ消すが、fdatasync/notify waitが次の支配要因になる。", 12, 500, "#52606d")
    svg.append('</svg>')
    out.write_text('\n'.join(svg))
    print(out)

if __name__ == "__main__":
    main()
