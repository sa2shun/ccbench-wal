#!/usr/bin/env python3
import csv
import sys
from pathlib import Path

THREADS = [1, 2, 4, 8, 16, 32]
EXPERIMENTS = [("normal_ycsb", "Normal YCSB"), ("abort0_ycsb", "Abort-0 YCSB")]
MODES = [("ermia_wal", "ERMIA+WAL"), ("ermia_pwal", "ERMIA+P-WAL")]
STACKS = [
    ("group_ermia_other_ns_pct", "ERMIA other", "#4c78a8"),
    ("group_ssn_ns_pct", "SSN", "#f58518"),
    ("group_wal_ns_pct", "WAL", "#54a24b"),
    ("group_unaccounted_ns_pct", "unaccounted", "#d8dee6"),
]
DETAILS = [
    ("read_ns_pct", "read"),
    ("update_ns_pct", "update"),
    ("ssn_finalize_pi_ns_pct", "ssn pi"),
    ("ssn_finalize_eta_ns_pct", "ssn eta"),
    ("ssn_exclusion_ns_pct", "ssn excl"),
    ("wal_log_ns_pct", "wal log"),
    ("wal_mutex_wait_ns_pct", "wal mutex"),
    ("wal_fdatasync_ns_pct", "wal fsync"),
    ("wal_notify_wait_ns_pct", "wal notify"),
]


def load(path):
    lines = path.read_text().splitlines()
    return list(csv.DictReader(lines[lines.index("===== SUMMARY_CSV =====") + 1:]))


def f(row, key):
    try:
        return float(row.get(key, 0) or 0)
    except Exception:
        return 0.0


def row_for(data, exp, mode, th):
    for r in data:
        if r["experiment"] == exp and r["mode"] == mode and int(r["thread_num"]) == th:
            return r
    raise KeyError((exp, mode, th))


def draw_panel(svg, data, exp, mode, title, x, y, w, h):
    base = y + h - 44
    plot_h = h - 82
    bar_w = 30
    gap = (w - 70 - len(THREADS) * bar_w) / (len(THREADS) - 1)
    svg.append(f'<text x="{x}" y="{y}" font-size="15" font-weight="800" fill="#17212b">{title}</text>')
    svg.append(f'<line x1="{x+42}" y1="{base}" x2="{x+w}" y2="{base}" stroke="#c8d0d9"/>')
    for tick in [0, 50, 100]:
        yy = base - plot_h * tick / 100.0
        svg.append(f'<line x1="{x+42}" y1="{yy:.1f}" x2="{x+w}" y2="{yy:.1f}" stroke="#edf1f5"/>')
        svg.append(f'<text x="{x+35}" y="{yy+4:.1f}" text-anchor="end" font-size="9" fill="#667789">{tick}</text>')
    for i, th in enumerate(THREADS):
        r = row_for(data, exp, mode, th)
        bx = x + 54 + i * (bar_w + gap)
        top = base
        for key, _, color in STACKS:
            pct = min(100.0, max(0.0, f(r, key)))
            bh = plot_h * pct / 100.0
            top -= bh
            svg.append(f'<rect x="{bx:.1f}" y="{top:.1f}" width="{bar_w}" height="{bh:.1f}" fill="{color}"/>')
        svg.append(f'<text x="{bx+bar_w/2:.1f}" y="{base+15}" text-anchor="middle" font-size="9" fill="#34495e">{th}</text>')
        svg.append(f'<text x="{bx+bar_w/2:.1f}" y="{base+28}" text-anchor="middle" font-size="8" fill="#637083">{float(r["throughput_tps"] or 0):.0f}</text>')
    # detail table for 32 threads
    r32 = row_for(data, exp, mode, 32)
    tx = x + w - 170
    ty = y + 18
    svg.append(f'<text x="{tx}" y="{ty}" font-size="10" font-weight="700" fill="#17212b">32-thread detail</text>')
    for idx, (key, label) in enumerate(DETAILS):
        svg.append(f'<text x="{tx}" y="{ty+15+idx*12}" font-size="9" fill="#52606d">{label}</text>')
        svg.append(f'<text x="{tx+112}" y="{ty+15+idx*12}" text-anchor="end" font-size="9" fill="#17212b">{f(r32,key):.1f}%</text>')


def main():
    path = Path(sys.argv[1])
    data = load(path)
    out = path.with_name(path.stem + "_dashboard.svg")
    width = 1500
    height = 1040
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#f7f9fb"/>',
        '<text x="34" y="42" font-size="28" font-weight="800" fill="#17212b">Hierarchical ERMIA/WAL Time Breakdown</text>',
        '<text x="34" y="68" font-size="13" fill="#52606d">100% = actual elapsed time × thread count. Bars show accumulated thread-time share; numbers below bars are throughput[tps].</text>',
    ]
    lx = 34
    for _, label, color in STACKS:
        svg.append(f'<rect x="{lx}" y="92" width="13" height="13" fill="{color}"/>')
        svg.append(f'<text x="{lx+19}" y="104" font-size="12" fill="#34495e">{label}</text>')
        lx += 150
    panel_w = 700
    panel_h = 210
    top = 145
    gap_y = 80
    for row_i, (exp, exp_title) in enumerate(EXPERIMENTS):
        y = top + row_i * (panel_h + gap_y + 230)
        svg.append(f'<text x="34" y="{y-28}" font-size="20" font-weight="800" fill="#17212b">{exp_title}</text>')
        for col_i, (mode, mode_title) in enumerate(MODES):
            x = 38 + col_i * (panel_w + 40)
            svg.append(f'<rect x="{x-12}" y="{y-20}" width="{panel_w+18}" height="{panel_h+36}" fill="#ffffff" stroke="#dfe6ee" rx="6"/>')
            draw_panel(svg, data, exp, mode, mode_title, x, y, panel_w, panel_h)
    svg.append('<text x="34" y="1010" font-size="11" fill="#52606d">ERMIA other includes read/update/insert/delete, node validation, version install, cleanup, maintenance, and abort cleanup. WAL detail is from WalLogger internal timers.</text>')
    svg.append('</svg>')
    out.write_text("\n".join(svg))
    print(out)


if __name__ == "__main__":
    main()
