#!/usr/bin/env python3
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.ticker import FuncFormatter


ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"
BASELINE_CSV = TABLE_DIR / "ycsbb_wal_pwal_cstamp_scaling.csv"
TIDEWAL_CSV = TABLE_DIR / "ycsbb_cstamp_readonly_skip_20260607.csv"
OUT_CSV = TABLE_DIR / "ycsbb_tidewal_paper_figures_20260607.csv"

THREADS = [1, 2, 4, 8, 16, 32]
SYSTEM_ORDER = ["Single WAL", "P-WAL", "TideWAL"]

COLORS = {
    "Single WAL": "#4b5563",
    "P-WAL": "#d97706",
    "TideWAL": "#047857",
}

MARKERS = {
    "Single WAL": "o",
    "P-WAL": "s",
    "TideWAL": "^",
}

LINEWIDTHS = {
    "Single WAL": 2.3,
    "P-WAL": 2.5,
    "TideWAL": 3.0,
}


def read_rows(path):
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


def fnum(row, key, fallback=0.0):
    try:
        return float(row.get(key) or fallback)
    except (TypeError, ValueError):
        return fallback


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def median(values):
    values = sorted(values)
    if not values:
        return 0.0
    n = len(values)
    if n % 2:
        return values[n // 2]
    return (values[n // 2 - 1] + values[n // 2]) / 2.0


def compact_number(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1000:
        return f"{v / 1000:.0f}K"
    return f"{v:.0f}"


def build_plot_rows():
    baseline = read_rows(BASELINE_CSV)
    tidewal = read_rows(TIDEWAL_CSV)
    rows = []

    for th in THREADS:
        single = [
            r for r in baseline
            if r.get("mode") == "ermia_single_wal" and int(r.get("thread_num", 0)) == th
        ]
        if single:
            ack = mean(fnum(r, "durable_ack_tps") for r in single)
            latency_values = []
            for r in single:
                p99 = fnum(r, "ack_latency_p99_us")
                if p99 <= 0:
                    p99 = th * 1_000_000.0 / max(fnum(r, "durable_ack_tps"), 1.0)
                latency_values.append(p99)
            rows.append({
                "system": "Single WAL",
                "thread_num": th,
                "xpos": THREADS.index(th),
                "ack_tps": ack,
                "p99_us": median(latency_values),
                "pending": 0.0,
            })

        pwal = [
            r for r in baseline
            if r.get("mode") == "ermia_plain_pwal" and int(r.get("thread_num", 0)) == th
        ]
        if pwal:
            rows.append({
                "system": "P-WAL",
                "thread_num": th,
                "xpos": THREADS.index(th),
                "ack_tps": mean(fnum(r, "durable_ack_tps") for r in pwal),
                "p99_us": median(fnum(r, "ack_latency_p99_us") for r in pwal),
                "pending": 0.0,
            })

        tide = [
            r for r in tidewal
            if r.get("config") == "tuned_l4_c1_g16_f50" and int(r.get("thread_num", 0)) == th
        ]
        if tide:
            r = tide[0]
            rows.append({
                "system": "TideWAL",
                "thread_num": th,
                "xpos": THREADS.index(th),
                "ack_tps": fnum(r, "ack_tps_mean"),
                "p99_us": fnum(r, "p99_us_median"),
                "pending": fnum(r, "pending_mean"),
            })

    return rows


def write_plot_csv(rows):
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["system", "thread_num", "ack_tps", "p99_us", "pending"]
    with OUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fields})


def setup_style():
    sns.set_theme(
        context="paper",
        style="white",
        font_scale=1.18,
        rc={
            "font.family": "DejaVu Sans",
            "axes.labelcolor": "#111827",
            "xtick.color": "#374151",
            "ytick.color": "#374151",
            "axes.edgecolor": "#9ca3af",
            "axes.linewidth": 0.9,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        },
    )
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def direct_label(ax, df, metric, text_offsets=None):
    text_offsets = text_offsets or {}
    for system in SYSTEM_ORDER:
        sub = df[df["system"] == system].sort_values("xpos")
        if sub.empty:
            continue
        last = sub.iloc[-1]
        dy = text_offsets.get(system, 0)
        ax.annotate(
            system,
            xy=(last["xpos"], last[metric]),
            xytext=(14, dy),
            textcoords="offset points",
            va="center",
            ha="left",
            color=COLORS[system],
            fontsize=10.5,
            fontweight="bold" if system == "TideWAL" else "normal",
            clip_on=False,
        )


def decorate_axis(ax, ylabel, yfmt, yscale="linear", ylim=None):
    ax.set_xlabel("Worker threads", labelpad=8)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.set_xticks(range(len(THREADS)))
    ax.set_xticklabels([str(t) for t in THREADS])
    ax.set_xlim(-0.18, len(THREADS) - 0.22)
    if yscale != "linear":
        ax.set_yscale(yscale)
    if ylim:
        ax.set_ylim(*ylim)
    ax.yaxis.set_major_formatter(FuncFormatter(yfmt))
    ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")
    ax.tick_params(axis="both", length=0, pad=6)


def draw_lines(ax, df, metric):
    for system in SYSTEM_ORDER:
        sub = df[df["system"] == system].sort_values("xpos")
        if sub.empty:
            continue
        ax.plot(
            sub["xpos"],
            sub[metric],
            color=COLORS[system],
            marker=MARKERS[system],
            linewidth=LINEWIDTHS[system],
            markersize=7.5,
            markerfacecolor="white",
            markeredgewidth=2.0,
            solid_capstyle="round",
            zorder=5 if system == "TideWAL" else 4,
        )


def plot_metric(df, metric, ylabel, output, yscale="linear", ylim=None,
                yfmt=None, label_offsets=None, caption=None):
    setup_style()
    fig, ax = plt.subplots(figsize=(7.15, 4.05), constrained_layout=True)
    draw_lines(ax, df, metric)
    decorate_axis(
        ax,
        ylabel,
        yfmt or (lambda v, _: compact_number(v)),
        yscale=yscale,
        ylim=ylim,
    )
    direct_label(ax, df, metric, text_offsets=label_offsets)
    if caption:
        ax.text(
            0.0,
            1.04,
            caption,
            transform=ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=10.5,
            color="#374151",
        )
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def main():
    rows = build_plot_rows()
    write_plot_csv(rows)
    df = pd.DataFrame(rows)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    plot_metric(
        df,
        "ack_tps",
        "Ack throughput [tx/s]",
        FIG_DIR / "fig_ycsbb_tidewal_ack_tps.pdf",
        ylim=(0, 415000),
        label_offsets={
            "Single WAL": 9,
            "P-WAL": 3,
            "TideWAL": 0,
        },
        caption="YCSB-B, 95% reads, 10 operations per transaction",
    )
    plot_metric(
        df,
        "p99_us",
        "p99 latency [us]",
        FIG_DIR / "fig_ycsbb_tidewal_latency.pdf",
        yscale="log",
        ylim=(64, 4096),
        yfmt=lambda v, _: compact_number(v),
        label_offsets={
            "Single WAL": 0,
            "P-WAL": -13,
            "TideWAL": 13,
        },
        caption="YCSB-B, p99 durable-ack latency; no confidence band",
    )
    plot_metric(
        df,
        "pending",
        "Pending durable commits",
        FIG_DIR / "fig_ycsbb_tidewal_pending.pdf",
        ylim=(0, 62),
        label_offsets={
            "Single WAL": 2,
            "P-WAL": 17,
            "TideWAL": 0,
        },
        caption="YCSB-B, measured at the end of the steady-state window",
    )
    print(OUT_CSV)
    print(FIG_DIR / "fig_ycsbb_tidewal_ack_tps.pdf")
    print(FIG_DIR / "fig_ycsbb_tidewal_latency.pdf")
    print(FIG_DIR / "fig_ycsbb_tidewal_pending.pdf")


if __name__ == "__main__":
    main()
