#!/usr/bin/env python3
import csv
import html
import sys
from pathlib import Path


RC = "#2563eb"
ERMIA = "#dc2626"
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
        for k in ("throughput_tps", "latency_ns", "abort_rate"):
            row[k] = float(row[k]) if row[k] else None
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
    return f"{v * 100:.0f}%"


def panel(svg, x, y, w, h, title, rows, metric, ylabel, formatter):
    pad_l, pad_r, pad_t, pad_b = 54, 16, 28, 34
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    threads = [1, 2, 4, 8, 16, 32]
    colors = {"rc": RC, "ermia": ERMIA}
    vals = [r[metric] for r in rows if r[metric] is not None]
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

    for proto in ("rc", "ermia"):
        pts = []
        for i, th in enumerate(threads):
            row = next((r for r in rows if r["protocol"] == proto and r["thread_num"] == th), None)
            if row is None or row[metric] is None:
                if len(pts) > 1:
                    svg.append(f'<polyline points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="none" stroke="{colors[proto]}" stroke-width="2.4"/>')
                pts = []
                continue
            vx = px + pw * i / (len(threads) - 1)
            vy = py + ph - ph * row[metric] / max_y
            pts.append((vx, vy))
            svg.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="3.2" fill="{colors[proto]}"/>')
        if len(pts) > 1:
            svg.append(f'<polyline points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="none" stroke="{colors[proto]}" stroke-width="2.4"/>')


def speedup(svg, x, y, w, h, title, rows):
    pad_l, pad_r, pad_t, pad_b = 46, 14, 28, 32
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    threads = [1, 2, 4, 8, 16, 32]
    vals = []
    for th in threads:
        rc = next((r["throughput_tps"] for r in rows if r["protocol"] == "rc" and r["thread_num"] == th), None)
        er = next((r["throughput_tps"] for r in rows if r["protocol"] == "ermia" and r["thread_num"] == th), None)
        vals.append(rc / er if rc and er else None)
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
        svg.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{RC}" opacity="0.85"/>')
        svg.append(f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" font-size="9" text-anchor="middle" fill="{TEXT}">{vals[i]:.2f}x</text>')


def render(rows, out):
    scenarios = []
    for r in rows:
        if r["scenario"] not in scenarios:
            scenarios.append(r["scenario"])
    width, panel_w, panel_h, gap = 1560, 360, 220, 18
    top, left = 74, 24
    height = top + len(scenarios) * (panel_h + gap) + 28
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f3f4f6"/>',
        f'<text x="24" y="34" font-size="24" font-weight="800" fill="{TEXT}">TPC-C RC vs ERMIA comparison</text>',
        f'<text x="24" y="56" font-size="13" fill="{MUTED}">extime=10s, tpcc_num_wh=1, gc_inter_us=100000000. Missing points indicate segfaulted runs.</text>',
        f'<circle cx="1210" cy="32" r="5" fill="{RC}"/><text x="1220" y="36" font-size="13" fill="{TEXT}">RC</text>',
        f'<circle cx="1270" cy="32" r="5" fill="{ERMIA}"/><text x="1280" y="36" font-size="13" fill="{TEXT}">ERMIA</text>',
    ]
    for i, scenario in enumerate(scenarios):
        y = top + i * (panel_h + gap)
        subset = [r for r in rows if r["scenario"] == scenario]
        panel(svg, left, y, panel_w, panel_h, f"{scenario}: throughput", subset, "throughput_tps", "tps", fmt_num)
        panel(svg, left + panel_w + gap, y, panel_w, panel_h, f"{scenario}: latency", subset, "latency_ns", "ns", fmt_num)
        panel(svg, left + 2 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: abort rate", subset, "abort_rate", "abort", fmt_rate)
        speedup(svg, left + 3 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: RC / ERMIA throughput", subset)
    svg.append("</svg>")
    Path(out).write_text("\n".join(svg))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: plot_tpcc_comparison.py result.txt output.svg")
    render(load_summary(sys.argv[1]), sys.argv[2])
