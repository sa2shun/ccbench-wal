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


def rows(path):
    lines = path.read_text().splitlines()
    return list(csv.DictReader(lines[lines.index("===== SUMMARY_CSV =====") + 1:]))


def panel(svg, data, experiment, title, x, y, w, h):
    subset = [r for r in data if r["experiment"] == experiment and int(r["thread_num"]) == 32]
    modes = [r["mode"] for r in subset]
    bar_w = 110
    gap = 80
    base = y + h - 58
    plot_h = h - 112
    svg.append(f'<text x="{x}" y="{y+22}" font-size="20" font-weight="700" fill="#152534">{title}</text>')
    svg.append(f'<text x="{x}" y="{y+44}" font-size="12" fill="#586879">32 threads, stacked by accounted time percentage</text>')
    for i, r in enumerate(subset):
        bx = x + 92 + i * (bar_w + gap)
        top = base
        for field, label, color in FIELDS:
            pct = float(r[field])
            bh = plot_h * pct / 100.0
            top -= bh
            svg.append(f'<rect x="{bx}" y="{top:.1f}" width="{bar_w}" height="{bh:.1f}" fill="{color}"/>')
            if pct >= 7:
                svg.append(f'<text x="{bx+bar_w/2}" y="{top+bh/2+4:.1f}" text-anchor="middle" font-size="11" fill="#fff">{pct:.0f}%</text>')
        svg.append(f'<text x="{bx+bar_w/2}" y="{base+22}" text-anchor="middle" font-size="12" fill="#334455">{r["mode"]}</text>')
        svg.append(f'<text x="{bx+bar_w/2}" y="{base+39}" text-anchor="middle" font-size="11" fill="#586879">{float(r["throughput_tps"]):.0f} tps</text>')
    for j in range(5):
        yy = base - plot_h * j / 4
        svg.append(f'<line x1="{x+54}" y1="{yy:.1f}" x2="{x+w-34}" y2="{yy:.1f}" stroke="#e1e6eb"/>')
        svg.append(f'<text x="{x+46}" y="{yy+4:.1f}" text-anchor="end" font-size="10" fill="#586879">{j*25}%</text>')


def main():
    path = Path(sys.argv[1])
    data = rows(path)
    out = path.with_name(path.stem + "_dashboard.svg")
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="760" viewBox="0 0 1280 760">',
        '<rect width="1280" height="760" fill="#f7f9fb"/>',
        '<text x="34" y="42" font-size="28" font-weight="800" fill="#152534">WAL Cost Breakdown</text>',
        '<text x="34" y="68" font-size="13" fill="#586879">Instrumented time: payload generation, mutex wait, write, fdatasync, and notification wait.</text>',
    ]
    lx = 34
    for _, label, color in FIELDS:
        svg.append(f'<rect x="{lx}" y="92" width="13" height="13" fill="{color}"/>')
        svg.append(f'<text x="{lx+19}" y="104" font-size="12" fill="#334455">{label}</text>')
        lx += 150
    svg += [
        '<rect x="26" y="130" width="600" height="560" fill="#ffffff" stroke="#e1e6eb" rx="6"/>',
        '<rect x="654" y="130" width="600" height="560" fill="#ffffff" stroke="#e1e6eb" rx="6"/>',
    ]
    panel(svg, data, "abort0_ycsb", "Abort-0 ERMIA WAL/P-WAL", 48, 154, 556, 504)
    panel(svg, data, "wal_framework", "No-CC WAL Framework", 676, 154, 556, 504)
    svg.append('</svg>')
    out.write_text("\n".join(svg))
    print(out)


if __name__ == "__main__":
    main()
