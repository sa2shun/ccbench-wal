#!/usr/bin/env python3
import csv
import html
import sys
from pathlib import Path


RC_COLOR = "#2563eb"
ERMIA_COLOR = "#dc2626"
GRID = "#e5e7eb"
TEXT = "#111827"
MUTED = "#6b7280"


def load_summary(path):
    lines = Path(path).read_text().splitlines()
    try:
        start = lines.index("===== SUMMARY_CSV =====") + 1
    except ValueError:
        raise SystemExit("SUMMARY_CSV marker not found")

    rows = []
    for row in csv.DictReader(lines[start:]):
        if not row:
            continue
        row["thread_num"] = int(row["thread_num"])
        row["throughput_tps"] = float(row["throughput_tps"])
        row["latency_ns"] = float(row["latency_ns"])
        row["abort_rate"] = float(row["abort_rate"])
        rows.append(row)
    return rows


def fmt_num(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}k"
    return f"{v:.0f}"


def fmt_rate(v):
    return f"{v * 100:.1f}%"


def point_path(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def panel(svg, x, y, w, h, title, rows, metric, y_label, formatter):
    pad_l, pad_r, pad_t, pad_b = 48, 16, 28, 34
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    threads = [1, 2, 4, 8, 16, 32]
    protocols = ["rc", "ermia"]
    colors = {"rc": RC_COLOR, "ermia": ERMIA_COLOR}
    data = {
        p: {r["thread_num"]: r[metric] for r in rows if r["protocol"] == p}
        for p in protocols
    }
    max_y = max(max(vals.values()) for vals in data.values() if vals)
    if metric == "abort_rate":
        max_y = max(max_y, 0.01)
    max_y *= 1.12

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
    svg.append(f'<text x="{x + 13}" y="{py + ph / 2:.1f}" font-size="10" text-anchor="middle" fill="{MUTED}" transform="rotate(-90 {x + 13} {py + ph / 2:.1f})">{html.escape(y_label)}</text>')

    for p in protocols:
        pts = []
        for i, th in enumerate(threads):
            vx = px + pw * i / (len(threads) - 1)
            vy = py + ph - ph * data[p][th] / max_y
            pts.append((vx, vy))
        svg.append(f'<polyline points="{point_path(pts)}" fill="none" stroke="{colors[p]}" stroke-width="2.4"/>')
        for vx, vy in pts:
            svg.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="3.2" fill="{colors[p]}"/>')


def speedup_panel(svg, x, y, w, h, scenario, rows):
    pad_l, pad_r, pad_t, pad_b = 46, 14, 28, 32
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    threads = [1, 2, 4, 8, 16, 32]
    rc = {r["thread_num"]: r["throughput_tps"] for r in rows if r["protocol"] == "rc"}
    er = {r["thread_num"]: r["throughput_tps"] for r in rows if r["protocol"] == "ermia"}
    vals = [rc[t] / er[t] for t in threads]
    max_y = max(1.5, max(vals) * 1.15)

    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="white" stroke="#d1d5db"/>')
    svg.append(f'<text x="{x + 14}" y="{y + 19}" font-size="13" font-weight="700" fill="{TEXT}">{scenario}: RC / ERMIA throughput</text>')
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
        bh = ph * vals[i] / max_y
        by = py + ph - bh
        svg.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{RC_COLOR}" opacity="0.85"/>')
        svg.append(f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" font-size="9" text-anchor="middle" fill="{TEXT}">{vals[i]:.2f}x</text>')
        svg.append(f'<text x="{bx + bar_w / 2:.1f}" y="{py + ph + 18}" font-size="10" text-anchor="middle" fill="{MUTED}">{th}</text>')


def render(rows, out_path):
    scenarios = ["base", "read_heavy", "write_heavy", "skewed"]
    by_scenario = {s: [r for r in rows if r["scenario"] == s] for s in scenarios}
    width = 1560
    panel_w = 360
    panel_h = 220
    gap = 18
    top = 74
    left = 24
    height = top + len(scenarios) * (panel_h + gap) + 260

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f3f4f6"/>',
        f'<text x="24" y="34" font-size="24" font-weight="800" fill="{TEXT}">YCSB RC vs ERMIA comparison</text>',
        f'<text x="24" y="56" font-size="13" fill="{MUTED}">extime=10s, tuple_num=1000, max_ope=10. Lines compare protocols across thread counts.</text>',
        f'<circle cx="1210" cy="32" r="5" fill="{RC_COLOR}"/><text x="1220" y="36" font-size="13" fill="{TEXT}">RC</text>',
        f'<circle cx="1270" cy="32" r="5" fill="{ERMIA_COLOR}"/><text x="1280" y="36" font-size="13" fill="{TEXT}">ERMIA</text>',
    ]

    for row_i, scenario in enumerate(scenarios):
        y = top + row_i * (panel_h + gap)
        rows_s = by_scenario[scenario]
        panel(svg, left, y, panel_w, panel_h, f"{scenario}: throughput", rows_s, "throughput_tps", "tps", fmt_num)
        panel(svg, left + (panel_w + gap), y, panel_w, panel_h, f"{scenario}: latency", rows_s, "latency_ns", "ns", fmt_num)
        panel(svg, left + 2 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: abort rate", rows_s, "abort_rate", "abort", fmt_rate)
        speedup_panel(svg, left + 3 * (panel_w + gap), y, panel_w, panel_h, scenario, rows_s)

    svg.append("</svg>")
    Path(out_path).write_text("\n".join(svg))


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: plot_ycsb_comparison.py result.txt output.svg")
    rows = load_summary(sys.argv[1])
    render(rows, sys.argv[2])


if __name__ == "__main__":
    main()
