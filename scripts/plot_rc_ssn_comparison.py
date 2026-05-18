#!/usr/bin/env python3
import csv
import html
import sys
from pathlib import Path


COLORS = {
    "rc": "#2563eb",
    "rc_ssn": "#16a34a",
    "ermia": "#dc2626",
}
GRID = "#e5e7eb"
TEXT = "#111827"
MUTED = "#6b7280"


def load_summary(path):
    lines = Path(path).read_text().splitlines()
    start = lines.index("===== SUMMARY_CSV =====") + 1
    rows = []
    for row in csv.DictReader(lines[start:]):
        if not row:
            continue
        row["thread_num"] = int(row["thread_num"])
        row["exit_code"] = int(row["exit_code"])
        for key in ("throughput_tps", "latency_ns", "abort_rate"):
            row[key] = float(row[key]) if row[key] and row["exit_code"] == 0 else None
        rows.append(row)
    return rows


def fmt_num(v):
    if v is None:
        return ""
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}k"
    return f"{v:.0f}"


def fmt_rate(v):
    return f"{v * 100:.1f}%"


def polyline(svg, points, color):
    if len(points) > 1:
        svg.append(
            f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" '
            f'fill="none" stroke="{color}" stroke-width="2.4"/>'
        )


def panel(svg, x, y, w, h, title, rows, protocols, metric, ylabel, formatter):
    pad_l, pad_r, pad_t, pad_b = 54, 16, 28, 34
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    threads = [1, 2, 4, 8, 16, 32]
    vals = [r[metric] for r in rows if r["protocol"] in protocols and r[metric] is not None]
    max_y = max(vals) * 1.15 if vals else 1.0
    if metric == "abort_rate":
        max_y = max(max_y, 0.01)

    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="white" stroke="#d1d5db"/>')
    svg.append(f'<text x="{x + 14}" y="{y + 19}" font-size="13" font-weight="700" fill="{TEXT}">{html.escape(title)}</text>')
    for i in range(5):
        gy = py + ph - ph * i / 4
        val = max_y * i / 4
        svg.append(f'<line x1="{px}" y1="{gy:.1f}" x2="{px + pw}" y2="{gy:.1f}" stroke="{GRID}"/>')
        svg.append(f'<text x="{px - 8}" y="{gy + 4:.1f}" font-size="10" text-anchor="end" fill="{MUTED}">{formatter(val)}</text>')
    for i, th in enumerate(threads):
        gx = px + pw * i / (len(threads) - 1)
        svg.append(f'<text x="{gx:.1f}" y="{py + ph + 20}" font-size="10" text-anchor="middle" fill="{MUTED}">{th}</text>')
    svg.append(f'<text x="{px + pw / 2:.1f}" y="{y + h - 6}" font-size="10" text-anchor="middle" fill="{MUTED}">threads</text>')
    svg.append(f'<text x="{x + 13}" y="{py + ph / 2:.1f}" font-size="10" text-anchor="middle" fill="{MUTED}" transform="rotate(-90 {x + 13} {py + ph / 2:.1f})">{html.escape(ylabel)}</text>')

    for proto in protocols:
        points = []
        for i, th in enumerate(threads):
            row = next((r for r in rows if r["protocol"] == proto and r["thread_num"] == th), None)
            if row is None or row[metric] is None:
                polyline(svg, points, COLORS[proto])
                points = []
                continue
            vx = px + pw * i / (len(threads) - 1)
            vy = py + ph - ph * row[metric] / max_y
            points.append((vx, vy))
            svg.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="3.2" fill="{COLORS[proto]}"/>')
        polyline(svg, points, COLORS[proto])


def ratio_panel(svg, x, y, w, h, title, rows, numerator, denominator):
    pad_l, pad_r, pad_t, pad_b = 50, 14, 28, 32
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    threads = [1, 2, 4, 8, 16, 32]
    vals = []
    for th in threads:
        num = next((r["throughput_tps"] for r in rows if r["protocol"] == numerator and r["thread_num"] == th), None)
        den = next((r["throughput_tps"] for r in rows if r["protocol"] == denominator and r["thread_num"] == th), None)
        vals.append(num / den if num and den else None)
    max_y = max([v for v in vals if v is not None] or [1.0]) * 1.2
    max_y = max(max_y, 1.5)

    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="white" stroke="#d1d5db"/>')
    svg.append(f'<text x="{x + 14}" y="{y + 19}" font-size="13" font-weight="700" fill="{TEXT}">{html.escape(title)}</text>')
    for i in range(4):
        gy = py + ph - ph * i / 3
        val = max_y * i / 3
        svg.append(f'<line x1="{px}" y1="{gy:.1f}" x2="{px + pw}" y2="{gy:.1f}" stroke="{GRID}"/>')
        svg.append(f'<text x="{px - 8}" y="{gy + 4:.1f}" font-size="10" text-anchor="end" fill="{MUTED}">{val:.1f}x</text>')
    one_y = py + ph - ph / max_y
    svg.append(f'<line x1="{px}" y1="{one_y:.1f}" x2="{px + pw}" y2="{one_y:.1f}" stroke="#9ca3af" stroke-dasharray="4 4"/>')
    bar_w = pw / len(threads) * 0.58
    for i, th in enumerate(threads):
        bx = px + pw * (i + 0.5) / len(threads) - bar_w / 2
        svg.append(f'<text x="{bx + bar_w / 2:.1f}" y="{py + ph + 18}" font-size="10" text-anchor="middle" fill="{MUTED}">{th}</text>')
        if vals[i] is None:
            svg.append(f'<text x="{bx + bar_w / 2:.1f}" y="{py + ph / 2:.1f}" font-size="10" text-anchor="middle" fill="{MUTED}">fail</text>')
            continue
        bh = ph * vals[i] / max_y
        by = py + ph - bh
        svg.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{COLORS[numerator]}" opacity="0.85"/>')
        svg.append(f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" font-size="9" text-anchor="middle" fill="{TEXT}">{vals[i]:.2f}x</text>')


def render(rows, out, workload, left_proto, right_proto):
    scenarios = []
    for row in rows:
        if row["workload"] == workload and row["scenario"] not in scenarios:
            scenarios.append(row["scenario"])
    protocols = [left_proto, right_proto]
    width, panel_w, panel_h, gap = 1560, 360, 220, 18
    top, left = 74, 24
    height = top + len(scenarios) * (panel_h + gap) + 28
    title = f"{workload.upper()} {left_proto.upper()} vs {right_proto.upper()} comparison"
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f3f4f6"/>',
        f'<text x="24" y="34" font-size="24" font-weight="800" fill="{TEXT}">{html.escape(title)}</text>',
        f'<text x="24" y="56" font-size="13" fill="{MUTED}">extime=10s, threads=1/2/4/8/16/32. Missing points indicate failed runs.</text>',
        f'<circle cx="1210" cy="32" r="5" fill="{COLORS[left_proto]}"/><text x="1220" y="36" font-size="13" fill="{TEXT}">{left_proto.upper()}</text>',
        f'<circle cx="1300" cy="32" r="5" fill="{COLORS[right_proto]}"/><text x="1310" y="36" font-size="13" fill="{TEXT}">{right_proto.upper()}</text>',
    ]
    for i, scenario in enumerate(scenarios):
        y = top + i * (panel_h + gap)
        subset = [r for r in rows if r["workload"] == workload and r["scenario"] == scenario]
        panel(svg, left, y, panel_w, panel_h, f"{scenario}: throughput", subset, protocols, "throughput_tps", "tps", fmt_num)
        panel(svg, left + panel_w + gap, y, panel_w, panel_h, f"{scenario}: latency", subset, protocols, "latency_ns", "ns", fmt_num)
        panel(svg, left + 2 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: abort rate", subset, protocols, "abort_rate", "abort", fmt_rate)
        ratio_panel(svg, left + 3 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: {right_proto.upper()} / {left_proto.upper()} throughput", subset, right_proto, left_proto)
    svg.append("</svg>")
    Path(out).write_text("\n".join(svg))


def main():
    if len(sys.argv) != 5:
        raise SystemExit("usage: plot_rc_ssn_comparison.py result.txt workload left_proto right_proto")
    result = Path(sys.argv[1])
    workload = sys.argv[2]
    left_proto = sys.argv[3]
    right_proto = sys.argv[4]
    rows = load_summary(result)
    out = result.with_name(f"{result.stem}_{workload}_{left_proto}_vs_{right_proto}.svg")
    render(rows, out, workload, left_proto, right_proto)
    print(out)


if __name__ == "__main__":
    raise SystemExit(main())
