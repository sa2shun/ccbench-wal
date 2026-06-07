#!/usr/bin/env python3
import argparse
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

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
    "ermia_pwal_group_global_prefix": "#d62728",
    "ermia_async_global_lsn_prefix": "#1f77b4",
    "ermia_async_dep_frontier_cstamp": "#2ca02c",
}

MARKERS = {
    "ermia_pwal_group_global_prefix": "o",
    "ermia_async_global_lsn_prefix": "s",
    "ermia_async_dep_frontier_cstamp": "^",
}


def mode_params(args, mode):
    if args.same_params:
        return args.group_size, args.flush_us
    if mode == "ermia_pwal_group_global_prefix":
        return args.worker_wait_group_size, args.worker_wait_flush_us
    if mode == "ermia_async_global_lsn_prefix":
        return args.global_group_size, args.global_flush_us
    if mode == "ermia_async_dep_frontier_cstamp":
        return args.dep_group_size, args.dep_flush_us
    raise ValueError(f"unknown mode: {mode}")


def parse_int_list(text):
    return [int(x) for x in text.split(",") if x]


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

    plt.rcParams.update({
        "font.size": 10,
        "axes.labelsize": 11,
        "axes.titlesize": 13,
        "legend.fontsize": 9,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
    fig, ax = plt.subplots(figsize=(6.6, 4.1), constrained_layout=True)
    x = list(range(len(threads)))
    for mode in MODES:
        ys = [mean(grouped.get((mode, th), []), metric) for th in threads]
        yerr = [stdev(grouped.get((mode, th), []), metric) for th in threads]
        ax.errorbar(
            x,
            ys,
            yerr=yerr if metric == "durable_ack_tps" else None,
            label=LABELS[mode],
            color=COLORS[mode],
            marker=MARKERS[mode],
            linewidth=2.2,
            markersize=5.5,
            capsize=3 if metric == "durable_ack_tps" else 0,
        )
    ax.set_title(metric_title(metric))
    ax.set_xlabel("Worker threads")
    ax.set_ylabel(metric_label(metric))
    ax.set_xticks(x)
    ax.set_xticklabels([str(th) for th in threads])
    ax.grid(True, axis="y", color="#d9d9d9", linewidth=0.8)
    ax.grid(True, axis="x", color="#eeeeee", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if metric == "durable_ack_tps":
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_y(v)))
    elif metric == "ack_latency_p99_us":
        ax.set_yscale("log", base=2)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_y(v)))
    elif metric == "pending_commits":
        ax.set_yscale("symlog", linthresh=1, base=2)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_y(v)))
    subtitle = "YCSB-B, mode-tuned group/flush, logger=8, committer=1, max_pending=65536"
    ax.text(0.0, 1.01, subtitle, transform=ax.transAxes, fontsize=8, color="#555555")
    ax.legend(loc="best", frameon=False)
    fig.savefig(path)
    plt.close(fig)


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
        print(f"| parameter policy | {'same parameters' if args.same_params else 'mode-tuned'} |", file=f)
        print(f"| max_pending | {args.max_pending} |", file=f)
        print("", file=f)
        print("Mode-tuned parameters are used here because the goal of this figure is to show a representative operating point where ack throughput is comparable, while dependency frontier avoids the global-prefix backlog/tail-latency problem. This is not the single-parameter fairness plot; it is the presentation figure requested for the three-mode behavior.", file=f)
        print("", file=f)
        print("| mode | group_size | flush_us |", file=f)
        print("|---|---:|---:|", file=f)
        for mode in MODES:
            group_size, flush_us = mode_params(args, mode)
            print(f"| {LABELS[mode]} | {group_size} | {flush_us} |", file=f)
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
    parser.add_argument("--same-params", action="store_true")
    parser.add_argument("--worker-wait-group-size", type=int, default=8)
    parser.add_argument("--worker-wait-flush-us", type=int, default=100)
    parser.add_argument("--global-group-size", type=int, default=4)
    parser.add_argument("--global-flush-us", type=int, default=0)
    parser.add_argument("--dep-group-size", type=int, default=8)
    parser.add_argument("--dep-flush-us", type=int, default=100)
    parser.add_argument("--max-pending", type=int, default=65536)
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
                    group_size, flush_us = mode_params(args, mode)
                    row = run_case(
                        out_dir,
                        mode,
                        repeat,
                        th,
                        args.seconds,
                        preset["ycsb_max_ope"],
                        preset["ycsb_rratio"],
                        args.logger_num,
                        group_size,
                        flush_us,
                        args.max_pending,
                        remote_ppm=preset["remote_ppm"],
                        workload_kind=preset["binary"],
                        workload_mode="ycsb_b",
                        committer_num=args.committer_num,
                    )
                    rows.append(row)
                    print(
                        f"three_modes repeat={repeat} thread={th} mode={mode} "
                        f"group_size={group_size} flush_us={flush_us} "
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
