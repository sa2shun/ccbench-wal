#!/usr/bin/env python3
import csv
import sys
from pathlib import Path

COLORS = {"ermia_wal":"#386cb0", "ermia_pwal":"#ef7f28", "wal":"#386cb0", "pwal":"#ef7f28"}


def load(path):
    lines = path.read_text().splitlines()
    return list(csv.DictReader(lines[lines.index("===== SUMMARY_CSV =====") + 1:]))


def panel(rows, experiment, modes, title, x, y, w, h):
    threads = [1,2,4,8,16,32]
    vals = {(r["mode"], int(r["thread_num"])): float(r["throughput_tps"] or 0)
            for r in rows if r["experiment"] == experiment}
    top = max(vals.values()) * 1.12 if vals else 1
    plot_y = y + 44
    plot_h = h - 92
    group_w = (w - 70) / len(threads)
    bar_w = min(28, group_w * 0.33)
    out = [f'<text x="{x}" y="{y+20}" font-size="19" font-weight="700" fill="#152534">{title}</text>']
    for i in range(5):
        yy = plot_y + plot_h - plot_h*i/4
        val = top*i/4
        out.append(f'<line x1="{x+58}" y1="{yy:.1f}" x2="{x+w-16}" y2="{yy:.1f}" stroke="#dce3e9"/>')
        out.append(f'<text x="{x+50}" y="{yy+4:.1f}" text-anchor="end" font-size="10" fill="#586879">{val/1000:.0f}k</text>')
    for ti, th in enumerate(threads):
        cx = x + 70 + group_w * (ti + 0.5)
        for mi, mode in enumerate(modes):
            v = vals[(mode, th)]
            bh = plot_h * v / top
            bx = cx + (mi - 1) * bar_w
            by = plot_y + plot_h - bh
            out.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w-3:.1f}" height="{bh:.1f}" fill="{COLORS[mode]}" rx="2"/>')
        out.append(f'<text x="{cx-4:.1f}" y="{plot_y+plot_h+19:.1f}" text-anchor="middle" font-size="10" fill="#334455">{th}</text>')
    ratio_bits = []
    for th in threads:
        a = vals[(modes[0], th)]
        b = vals[(modes[1], th)]
        ratio_bits.append(f'{th}t {b/a:.2f}x' if a else f'{th}t n/a')
    out.append(f'<text x="{x+58}" y="{y+h-12}" font-size="11" fill="#586879">P-WAL/WAL: {"  ".join(ratio_bits)}</text>')
    return "\n".join(out)


def main():
    path = Path(sys.argv[1])
    rows = load(path)
    out = path.with_name(path.stem + "_dashboard.svg")
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="760" viewBox="0 0 1280 760">',
        '<rect width="1280" height="760" fill="#f7f9fb"/>',
        '<text x="34" y="44" font-size="28" font-weight="800" fill="#152534">WAL clean-world experiments</text>',
        '<text x="34" y="70" font-size="13" fill="#586879">Left: ERMIA with worker-partitioned YCSB key ranges. Right: no-CC WAL framework only. No injected delay.</text>',
        '<rect x="34" y="92" width="14" height="14" fill="#386cb0"/><text x="56" y="104" font-size="13" fill="#334455">WAL / shared WAL</text>',
        '<rect x="220" y="92" width="14" height="14" fill="#ef7f28"/><text x="242" y="104" font-size="13" fill="#334455">P-WAL / per-thread WAL</text>',
        '<rect x="26" y="130" width="600" height="560" fill="#ffffff" stroke="#e1e6eb" rx="6"/>',
        '<rect x="654" y="130" width="600" height="560" fill="#ffffff" stroke="#e1e6eb" rx="6"/>',
        panel(rows, "abort0_ycsb", ["ermia_wal", "ermia_pwal"], "Abort-0 ERMIA YCSB throughput", 48, 154, 556, 504),
        panel(rows, "wal_framework", ["wal", "pwal"], "No-CC WAL framework throughput", 676, 154, 556, 504),
        '</svg>'
    ]
    out.write_text("\n".join(svg))
    print(out)


if __name__ == "__main__":
    main()
