#!/usr/bin/env python3
import csv
import html
import sys
from pathlib import Path


TEXT = "#111827"
MUTED = "#6b7280"
GRID = "#e5e7eb"
THREADS = [1, 2, 4, 8, 16, 32]
PROTOCOLS = ["rc_ssn", "rc_ssn_pwal", "rc_ssn_pwal_d_v1", "rc_ssn_pwal_d_v2"]
COLORS = {
    "rc_ssn": "#111827",
    "rc_ssn_pwal": "#dc2626",
    "rc_ssn_pwal_d_v1": "#2563eb",
    "rc_ssn_pwal_d_v2": "#16a34a",
}
LABELS = {
    "rc_ssn": "RC+SSN (no WAL)",
    "rc_ssn_pwal": "RC+SSN+P-WAL prefix",
    "rc_ssn_pwal_d_v1": "D-V1 direct",
    "rc_ssn_pwal_d_v2": "D-V2 depVector",
}


def load_rows(path):
    lines = Path(path).read_text().splitlines()
    start = lines.index("===== SUMMARY_CSV =====") + 1
    rows = []
    for row in csv.DictReader(lines[start:]):
        row["thread_num"] = int(row["thread_num"])
        row["exit_code"] = int(row["exit_code"])
        for key in ("throughput_tps", "latency_ns", "abort_rate"):
            row[key] = float(row[key]) if row[key] and row["exit_code"] == 0 else None
        rows.append(row)
    return rows


def fmt_num(value):
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:.0f}"


def fmt_rate(value):
    return f"{value * 100:.0f}%"


def polyline(svg, points, color):
    if len(points) > 1:
        data = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        svg.append(f'<polyline points="{data}" fill="none" stroke="{color}" stroke-width="2.2"/>')


def panel(svg, x, y, w, h, title, rows, metric, ylabel, formatter):
    pad_l, pad_r, pad_t, pad_b = 55, 16, 29, 34
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    values = [r[metric] for r in rows if r[metric] is not None]
    maximum = max(values) * 1.15 if values else 1
    if metric == "abort_rate":
        maximum = max(maximum, 0.01)
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="white" stroke="#d1d5db"/>')
    svg.append(f'<text x="{x + 14}" y="{y + 20}" font-size="13" font-weight="700" fill="{TEXT}">{html.escape(title)}</text>')
    for index in range(5):
        gy = py + ph - ph * index / 4
        value = maximum * index / 4
        svg.append(f'<line x1="{px}" y1="{gy:.1f}" x2="{px + pw}" y2="{gy:.1f}" stroke="{GRID}"/>')
        svg.append(f'<text x="{px - 8}" y="{gy + 4:.1f}" font-size="10" text-anchor="end" fill="{MUTED}">{formatter(value)}</text>')
    for index, thread in enumerate(THREADS):
        gx = px + pw * index / (len(THREADS) - 1)
        svg.append(f'<text x="{gx:.1f}" y="{py + ph + 20}" font-size="10" text-anchor="middle" fill="{MUTED}">{thread}</text>')
    svg.append(f'<text x="{x + 13}" y="{py + ph / 2:.1f}" font-size="10" text-anchor="middle" fill="{MUTED}" transform="rotate(-90 {x + 13} {py + ph / 2:.1f})">{ylabel}</text>')
    for proto in PROTOCOLS:
        points = []
        for index, thread in enumerate(THREADS):
            row = next((item for item in rows if item["protocol"] == proto and item["thread_num"] == thread), None)
            if row is None or row[metric] is None:
                polyline(svg, points, COLORS[proto])
                points = []
                continue
            cx = px + pw * index / (len(THREADS) - 1)
            cy = py + ph - ph * row[metric] / maximum
            points.append((cx, cy))
            svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.1" fill="{COLORS[proto]}"/>')
        polyline(svg, points, COLORS[proto])


def speedup_panel(svg, x, y, w, h, title, rows):
    pad_l, pad_r, pad_t, pad_b = 48, 14, 29, 34
    px, py = x + pad_l, y + pad_t
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    values = {"rc_ssn_pwal_d_v1": [], "rc_ssn_pwal_d_v2": []}
    for proto in values:
        for thread in THREADS:
            base = next((r["throughput_tps"] for r in rows if r["protocol"] == "rc_ssn_pwal" and r["thread_num"] == thread), None)
            measured = next((r["throughput_tps"] for r in rows if r["protocol"] == proto and r["thread_num"] == thread), None)
            values[proto].append(measured / base if base and measured else None)
    valid = [v for entries in values.values() for v in entries if v is not None]
    maximum = max(max(valid or [1]), 1.2) * 1.15
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="white" stroke="#d1d5db"/>')
    svg.append(f'<text x="{x + 14}" y="{y + 20}" font-size="13" font-weight="700" fill="{TEXT}">{html.escape(title)}</text>')
    one_y = py + ph - ph / maximum
    svg.append(f'<line x1="{px}" y1="{one_y:.1f}" x2="{px + pw}" y2="{one_y:.1f}" stroke="#9ca3af" stroke-dasharray="4 4"/>')
    group_w = pw / len(THREADS)
    bar_w = group_w * 0.32
    for index, thread in enumerate(THREADS):
        center = px + group_w * (index + 0.5)
        svg.append(f'<text x="{center:.1f}" y="{py + ph + 20}" font-size="10" text-anchor="middle" fill="{MUTED}">{thread}</text>')
        for offset, proto in enumerate(("rc_ssn_pwal_d_v1", "rc_ssn_pwal_d_v2")):
            value = values[proto][index]
            if value is None:
                continue
            bx = center + (offset - 0.5) * bar_w - bar_w / 2
            height = ph * value / maximum
            by = py + ph - height
            svg.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{height:.1f}" fill="{COLORS[proto]}"/>')
            svg.append(f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" font-size="8" text-anchor="middle" fill="{TEXT}">{value:.2f}x</text>')


def render(rows, output):
    scenarios = []
    for row in rows:
        if row["scenario"] not in scenarios:
            scenarios.append(row["scenario"])
    width, panel_w, panel_h, gap = 1580, 370, 225, 18
    top, left = 100, 22
    height = top + len(scenarios) * (panel_h + gap) + 24
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f3f4f6"/>',
        f'<text x="22" y="34" font-size="24" font-weight="800" fill="{TEXT}">YCSB SSN-PWAL-D comparison</text>',
        f'<text x="22" y="57" font-size="13" fill="{MUTED}">RC+SSN, global-prefix P-WAL, direct wait (V1), dependency-closed depVector wait (V2). extime=10s.</text>',
    ]
    legend_x = 22
    for proto in PROTOCOLS:
        svg.append(f'<circle cx="{legend_x}" cy="81" r="5" fill="{COLORS[proto]}"/><text x="{legend_x + 10}" y="85" font-size="12" fill="{TEXT}">{LABELS[proto]}</text>')
        legend_x += 225
    for index, scenario in enumerate(scenarios):
        y = top + index * (panel_h + gap)
        subset = [row for row in rows if row["scenario"] == scenario]
        panel(svg, left, y, panel_w, panel_h, f"{scenario}: throughput", subset, "throughput_tps", "tps", fmt_num)
        panel(svg, left + panel_w + gap, y, panel_w, panel_h, f"{scenario}: latency", subset, "latency_ns", "ns", fmt_num)
        panel(svg, left + 2 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: abort rate", subset, "abort_rate", "abort", fmt_rate)
        speedup_panel(svg, left + 3 * (panel_w + gap), y, panel_w, panel_h, f"{scenario}: D / prefix TPS", subset)
    svg.append("</svg>")
    Path(output).write_text("\n".join(svg))


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: plot_ycsb_ssn_pwal_d_comparison.py result.txt")
    result = Path(sys.argv[1])
    output = result.with_name(f"{result.stem}_dashboard.svg")
    render(load_rows(result), output)
    print(output)


if __name__ == "__main__":
    main()
