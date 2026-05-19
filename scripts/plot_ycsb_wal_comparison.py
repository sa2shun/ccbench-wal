#!/usr/bin/env python3
import csv
import html
import sys
from pathlib import Path


TEXT = "#111827"
MUTED = "#6b7280"
GRID = "#e5e7eb"
COLORS = {
    "si_wal": "#2563eb",
    "si_pwal": "#60a5fa",
    "ermia_wal": "#dc2626",
    "ermia_pwal": "#f97316",
    "rc_wal": "#16a34a",
    "rc_pwal": "#84cc16",
}


def load_rows(path):
    lines = Path(path).read_text().splitlines()
    start = lines.index("===== SUMMARY_CSV =====") + 1
    rows = []
    for row in csv.DictReader(lines[start:]):
        row["thread_num"] = int(row["thread_num"])
        row["exit_code"] = int(row["exit_code"])
        for k in ("throughput_tps", "latency_ns", "abort_rate"):
            row[k] = float(row[k]) if row[k] and row["exit_code"] == 0 else None
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


def line(svg, pts, color):
    if len(pts) > 1:
        svg.append(
            f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" '
            f'fill="none" stroke="{color}" stroke-width="2.1"/>'
        )


def panel(svg, x, y, w, h, title, rows, protocols, metric, ylabel, formatter):
    pad_l, pad_r, pad_t, pad_b = 54, 18, 28, 34
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
        pts = []
        for i, th in enumerate(threads):
            row = next((r for r in rows if r["protocol"] == proto and r["thread_num"] == th), None)
            if row is None or row[metric] is None:
                line(svg, pts, COLORS[proto])
                pts = []
                continue
            vx = px + pw * i / (len(threads) - 1)
            vy = py + ph - ph * row[metric] / max_y
            pts.append((vx, vy))
            svg.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="3" fill="{COLORS[proto]}"/>')
        line(svg, pts, COLORS[proto])


def speedup_panel(svg, x, y, w, h, title, rows, base_proto, pwal_proto):
    pad_l, pad_r, pad_t, pad_b = 46, 14, 28, 32
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    threads = [1, 2, 4, 8, 16, 32]
    vals = []
    for th in threads:
        wal = next((r["throughput_tps"] for r in rows if r["protocol"] == base_proto and r["thread_num"] == th), None)
        pwal = next((r["throughput_tps"] for r in rows if r["protocol"] == pwal_proto and r["thread_num"] == th), None)
        vals.append(pwal / wal if wal and pwal else None)
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
            continue
        bh = ph * vals[i] / max_y
        by = py + ph - bh
        svg.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{COLORS[pwal_proto]}" opacity="0.85"/>')
        svg.append(f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" font-size="9" text-anchor="middle" fill="{TEXT}">{vals[i]:.2f}x</text>')


def render(rows, out, group):
    if group == "all":
        protocols = ["si_wal", "si_pwal", "ermia_wal", "ermia_pwal", "rc_wal", "rc_pwal"]
        title = "YCSB WAL / P-WAL comparison"
    else:
        protocols = [f"{group}_wal", f"{group}_pwal"]
        title = f"YCSB {group.upper()} WAL vs P-WAL"
    scenarios = []
    for r in rows:
        if r["scenario"] not in scenarios:
            scenarios.append(r["scenario"])
    width, panel_w, panel_h, gap = 1560, 360, 220, 18
    top, left = 96, 24
    height = top + len(scenarios) * (panel_h + gap) + 28
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f3f4f6"/>',
        f'<text x="24" y="34" font-size="24" font-weight="800" fill="{TEXT}">{html.escape(title)}</text>',
        f'<text x="24" y="56" font-size="13" fill="{MUTED}">extime=10s, tuple_num=1000, max_ope=10. Ratio panel shows P-WAL / WAL throughput.</text>',
    ]
    lx = 24
    for proto in protocols:
        svg.append(f'<circle cx="{lx}" cy="78" r="5" fill="{COLORS[proto]}"/><text x="{lx + 10}" y="82" font-size="12" fill="{TEXT}">{proto}</text>')
        lx += 118
    for i, scenario in enumerate(scenarios):
        y = top + i * (panel_h + gap)
        subset = [r for r in rows if r["scenario"] == scenario]
        panel(svg, left, y, panel_w, panel_h, f"{scenario}: throughput", subset, protocols, "throughput_tps", "tps", fmt_num)
        panel(svg, left + panel_w + gap, y, panel_w, panel_h, f"{scenario}: latency", subset, protocols, "latency_ns", "ns", fmt_num)
        panel(svg, left + 2 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: abort rate", subset, protocols, "abort_rate", "abort", fmt_rate)
        if group == "all":
            speedup_panel(svg, left + 3 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: RC P-WAL / WAL", subset, "rc_wal", "rc_pwal")
        else:
            speedup_panel(svg, left + 3 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: P-WAL / WAL", subset, f"{group}_wal", f"{group}_pwal")
    svg.append("</svg>")
    Path(out).write_text("\n".join(svg))


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: plot_ycsb_wal_comparison.py result.txt group(all|si|ermia|rc)")
    result = Path(sys.argv[1])
    group = sys.argv[2]
    out = result.with_name(f"{result.stem}_{group}.svg")
    render(load_rows(result), out, group)
    print(out)


if __name__ == "__main__":
    raise SystemExit(main())
