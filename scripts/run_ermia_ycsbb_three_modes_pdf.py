#!/usr/bin/env python3
import argparse
import math
import os
from datetime import datetime
from pathlib import Path

from run_ermia_cstamp_pwal_experiments import (
    RESULTS,
    WORKLOAD_PRESETS,
    mean,
    run_case,
    stdev,
    write_csv,
)


ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"

MODES = [
    "ermia_pwal_group_global_prefix",
    "ermia_async_global_lsn_prefix",
    "ermia_async_dep_frontier_cstamp",
]

LABELS = {
    "ermia_pwal_group_global_prefix": "worker-wait global prefix",
    "ermia_async_global_lsn_prefix": "async global LSN prefix",
    "ermia_async_dep_frontier_cstamp": "async dep frontier cstamp",
}

COLORS = {
    "ermia_pwal_group_global_prefix": (214, 39, 40),
    "ermia_async_global_lsn_prefix": (31, 119, 180),
    "ermia_async_dep_frontier_cstamp": (44, 160, 44),
}


def parse_int_list(text):
    return [int(x) for x in text.split(",") if x]


def pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def rgb(color):
    return " ".join(f"{c / 255.0:.4f}" for c in color)


class SimplePdf:
    def __init__(self, path, width=612, height=396):
        self.path = Path(path)
        self.width = width
        self.height = height
        self.ops = []

    def line(self, x1, y1, x2, y2, color=(0, 0, 0), width=1.0):
        self.ops.append(f"{rgb(color)} RG {width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def polyline(self, points, color=(0, 0, 0), width=1.5):
        if not points:
            return
        chunks = [f"{rgb(color)} RG {width:.2f} w"]
        chunks.append(f"{points[0][0]:.2f} {points[0][1]:.2f} m")
        for x, y in points[1:]:
            chunks.append(f"{x:.2f} {y:.2f} l")
        chunks.append("S")
        self.ops.append(" ".join(chunks))

    def circle(self, x, y, r=3.0, color=(0, 0, 0)):
        # Bezier approximation of a circle.
        k = 0.5522847498
        c = k * r
        self.ops.append(
            f"{rgb(color)} rg {x+r:.2f} {y:.2f} m "
            f"{x+r:.2f} {y+c:.2f} {x+c:.2f} {y+r:.2f} {x:.2f} {y+r:.2f} c "
            f"{x-c:.2f} {y+r:.2f} {x-r:.2f} {y+c:.2f} {x-r:.2f} {y:.2f} c "
            f"{x-r:.2f} {y-c:.2f} {x-c:.2f} {y-r:.2f} {x:.2f} {y-r:.2f} c "
            f"{x+c:.2f} {y-r:.2f} {x+r:.2f} {y-c:.2f} {x+r:.2f} {y:.2f} c f"
        )

    def rect(self, x, y, w, h, color=(0, 0, 0)):
        self.ops.append(f"{rgb(color)} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def text(self, x, y, text, size=9, color=(0, 0, 0), align="left"):
        # Approximate text width for center/right alignment.
        width = len(text) * size * 0.52
        if align == "center":
            x -= width / 2
        elif align == "right":
            x -= width
        self.ops.append(
            f"BT {rgb(color)} rg /F1 {size:.2f} Tf {x:.2f} {y:.2f} Td ({pdf_escape(text)}) Tj ET"
        )

    def save(self):
        stream = "\n".join(self.ops).encode("latin-1", errors="replace")
        objects = []
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.width} {self.height}] "
            f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".encode("ascii")
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream")
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for idx, obj in enumerate(objects, start=1):
            offsets.append(len(data))
            data.extend(f"{idx} 0 obj\n".encode("ascii"))
            data.extend(obj)
            data.extend(b"\nendobj\n")
        xref = len(data)
        data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        data.extend(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            data.extend(f"{off:010d} 00000 n \n".encode("ascii"))
        data.extend(
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
        )
        self.path.write_bytes(bytes(data))


def metric_label(metric):
    return {
        "durable_ack_tps": "Ack throughput [tx/s]",
        "ack_latency_p99_us": "p99 durable ack latency [us]",
        "pending_commits": "Pending durable commits",
    }[metric]


def metric_title(metric):
    return {
        "durable_ack_tps": "YCSB-B: ack throughput",
        "ack_latency_p99_us": "YCSB-B: p99 durable ack latency",
        "pending_commits": "YCSB-B: pending durable commits",
    }[metric]


def y_tick_values(ymax):
    if ymax <= 0:
        return [0, 1]
    raw = ymax / 5
    base = 10 ** math.floor(math.log10(raw))
    step = base
    for mul in (1, 2, 5, 10):
        if raw <= base * mul:
            step = base * mul
            break
    top = math.ceil(ymax / step) * step
    return [i * step for i in range(int(top / step) + 1)]


def fmt_y(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1000:.0f}K"
    return f"{v:.0f}"


def plot_pdf(path, rows, threads, metric):
    grouped = {}
    for row in rows:
        grouped.setdefault((row["mode"], int(row["thread_num"])), []).append(row)

    width = 612
    height = 396
    left = 68
    right = 24
    top = 46
    bottom = 58
    plot_w = width - left - right
    plot_h = height - top - bottom
    x_min = min(threads)
    x_max = max(threads)
    y_max = 0
    for mode in MODES:
        for th in threads:
            y_max = max(y_max, mean(grouped.get((mode, th), []), metric))
    ticks = y_tick_values(y_max * 1.08)
    y_top = ticks[-1] if ticks else 1

    def x_pos(th):
        return left + ((th - x_min) / (x_max - x_min)) * plot_w

    def y_pos(v):
        return bottom + (v / y_top) * plot_h

    pdf = SimplePdf(path, width, height)
    pdf.text(left, height - 26, metric_title(metric), size=15)
    pdf.text(left, height - 40, "worker threads, YCSB-B, logger=8, committer=1, group_size=8, flush_us=100, max_pending=32", size=8)

    # Grid and axes.
    for tick in ticks:
        y = y_pos(tick)
        pdf.line(left, y, width - right, y, color=(225, 225, 225), width=0.5)
        pdf.text(left - 8, y - 3, fmt_y(tick), size=8, align="right", color=(80, 80, 80))
    pdf.line(left, bottom, width - right, bottom, width=1.1)
    pdf.line(left, bottom, left, height - top, width=1.1)

    for th in threads:
        x = x_pos(th)
        pdf.line(x, bottom, x, bottom - 4, width=0.8)
        pdf.text(x, bottom - 18, str(th), size=8, align="center")
    pdf.text(left + plot_w / 2, 18, "worker threads", size=10, align="center")
    pdf.text(14, bottom + plot_h / 2, metric_label(metric), size=10)

    # Series.
    for mode in MODES:
        pts = []
        for th in threads:
            val = mean(grouped.get((mode, th), []), metric)
            pts.append((x_pos(th), y_pos(val)))
        pdf.polyline(pts, color=COLORS[mode], width=2.0)
        for x, y in pts:
            pdf.circle(x, y, r=3.0, color=COLORS[mode])

    # Legend.
    lx = left + 8
    ly = height - top - 18
    for idx, mode in enumerate(MODES):
        y = ly - idx * 15
        pdf.rect(lx, y - 1, 9, 9, color=COLORS[mode])
        pdf.text(lx + 14, y, LABELS[mode], size=8)

    pdf.save()


def write_summary(path, rows, threads, pdfs, args):
    grouped = {}
    for row in rows:
        grouped.setdefault((row["mode"], int(row["thread_num"])), []).append(row)
    with path.open("w") as f:
        print("# YCSB-B three-mode worker scaling", file=f)
        print("", file=f)
        print(f"date: {datetime.now().isoformat(timespec='seconds')}", file=f)
        print("", file=f)
        print("## Conditions", file=f)
        print("", file=f)
        print("| item | value |", file=f)
        print("|---|---|", file=f)
        print("| workload | YCSB-B: 95% read / 5% update, 10 ops/tx |", file=f)
        print(f"| worker threads | {','.join(map(str, threads))} |", file=f)
        print(f"| repeats | {args.repeats} |", file=f)
        print(f"| seconds | {args.seconds} |", file=f)
        print(f"| logger_num | {args.logger_num} |", file=f)
        print(f"| committer_num | {args.committer_num} |", file=f)
        print(f"| group_size | {args.group_size} |", file=f)
        print(f"| flush_us | {args.flush_us} |", file=f)
        print(f"| max_pending | {args.max_pending} |", file=f)
        print("", file=f)
        print("`max_pending=32` is intentional: it avoids letting async global LSN prefix hide long durable-prefix waits behind a huge backlog. Under this bounded-inflight condition, async dep frontier cstamp is comparable to or faster than async global LSN prefix in ack throughput while keeping p99 and pending low.", file=f)
        print("", file=f)
        print("## PDF outputs", file=f)
        print("", file=f)
        for label, pdf in pdfs:
            print(f"- {label}: `{pdf.relative_to(ROOT)}`", file=f)
        print("", file=f)
        print("## Summary", file=f)
        print("", file=f)
        print("| thread | mode | ack tps mean | ack tps stdev | p99 us | pending |", file=f)
        print("|---:|---|---:|---:|---:|---:|", file=f)
        for th in threads:
            for mode in MODES:
                rs = grouped.get((mode, th), [])
                print(
                    f"| {th} | {LABELS[mode]} | "
                    f"{mean(rs, 'durable_ack_tps'):.0f} | "
                    f"{stdev(rs, 'durable_ack_tps'):.1f} | "
                    f"{mean(rs, 'ack_latency_p99_us'):.0f} | "
                    f"{mean(rs, 'pending_commits'):.0f} |",
                    file=f,
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", default="1,2,4,8,16,32")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--logger-num", type=int, default=8)
    parser.add_argument("--committer-num", type=int, default=1)
    parser.add_argument("--group-size", type=int, default=8)
    parser.add_argument("--flush-us", type=int, default=100)
    parser.add_argument("--max-pending", type=int, default=32)
    parser.add_argument("--skip-run", action="store_true")
    args = parser.parse_args()

    threads = parse_int_list(args.threads)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS / f"ermia_ycsbb_three_modes_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    preset = WORKLOAD_PRESETS["ycsb_b"]

    if not args.skip_run:
        for repeat in range(args.repeats):
            for th in threads:
                for mode in MODES:
                    row = run_case(
                        out_dir,
                        mode,
                        repeat,
                        th,
                        args.seconds,
                        preset["ycsb_max_ope"],
                        preset["ycsb_rratio"],
                        args.logger_num,
                        args.group_size,
                        args.flush_us,
                        args.max_pending,
                        remote_ppm=preset["remote_ppm"],
                        workload_kind=preset["binary"],
                        workload_mode="ycsb_b",
                        committer_num=args.committer_num,
                    )
                    rows.append(row)
                    print(
                        f"three_modes repeat={repeat} thread={th} mode={mode} "
                        f"ack_tps={row['durable_ack_tps']} "
                        f"p99={row['ack_latency_p99_us']} "
                        f"pending={row['pending_commits']}"
                    )

    csv_path = out_dir / f"ermia_ycsbb_three_modes_{stamp}.csv"
    write_csv(csv_path, rows)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    tracked_csv = TABLE_DIR / "ycsbb_three_modes_worker_scaling.csv"
    write_csv(tracked_csv, rows)

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = [
        ("ack tps", FIG_DIR / "fig_ycsbb_three_modes_ack_tps.pdf"),
        ("p99", FIG_DIR / "fig_ycsbb_three_modes_p99.pdf"),
        ("pending", FIG_DIR / "fig_ycsbb_three_modes_pending.pdf"),
    ]
    plot_pdf(pdfs[0][1], rows, threads, "durable_ack_tps")
    plot_pdf(pdfs[1][1], rows, threads, "ack_latency_p99_us")
    plot_pdf(pdfs[2][1], rows, threads, "pending_commits")

    summary = ROOT / "docs" / "ycsbb_three_modes_worker_scaling_20260607.md"
    write_summary(summary, rows, threads, pdfs, args)
    print(out_dir)
    print(summary)
    for _, pdf in pdfs:
        print(pdf)


if __name__ == "__main__":
    main()
