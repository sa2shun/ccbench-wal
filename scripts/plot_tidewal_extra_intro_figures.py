#!/usr/bin/env python3
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "paper" / "figures"

MAX_PENDING_CSV = (
    ROOT
    / "paper"
    / "tables"
    / "tidewal_extra_max_pending_20260607.csv"
)
CSTAMP_CSV = (
    ROOT
    / "paper"
    / "tables"
    / "tidewal_extra_cstamp_lsn_ablation_20260607.csv"
)
FAIR_CSV = ROOT / "paper" / "tables" / "ycsbabc_tidewal_fair_total_20260607.csv"


def set_style():
    sns.set_theme(
        context="paper",
        style="whitegrid",
        font="DejaVu Sans",
        rc={
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.labelcolor": "#111827",
            "xtick.color": "#374151",
            "ytick.color": "#374151",
            "grid.color": "#e5e7eb",
            "legend.frameon": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        },
    )


def save(fig, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(path)


def plot_max_pending_pareto(out):
    df = pd.read_csv(MAX_PENDING_CSV)
    agg = df.sort_values(["mode", "max_pending"]).copy()
    agg["mode_label"] = agg["mode"]
    agg["ack_ktps"] = agg["ack_tps"] / 1000.0

    palette = {
        "Async global prefix": "#4f46e5",
        "TideWAL": "#059669",
    }
    fig, ax = plt.subplots(figsize=(5.9, 3.6))
    sns.lineplot(
        data=agg,
        x="p99_us",
        y="ack_ktps",
        hue="mode_label",
        style="mode_label",
        markers=True,
        dashes=False,
        linewidth=2.2,
        markersize=7,
        palette=palette,
        ax=ax,
    )
    for _, row in agg.iterrows():
        ax.annotate(
            f"{int(row.max_pending):,}",
            (row.p99_us, row.ack_ktps),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=7.5,
            color="#374151",
        )
    ax.set_xscale("log")
    ax.set_xlabel("p99 durable-ack latency [us]")
    ax.set_ylabel("Ack throughput [K tx/s]")
    ax.set_title("Max-pending Pareto frontier")
    ax.legend(title="", loc="best")
    save(fig, out)


def plot_cstamp_lsn_ablation(out):
    df = pd.read_csv(CSTAMP_CSV)
    agg = df.copy()
    agg["mode_label"] = agg["mode"]
    cond_order = ["real_io", "io_light"]
    agg["condition"] = pd.Categorical(agg["condition"], cond_order, ordered=True)
    agg["condition_label"] = agg["condition"].map({"real_io": "Real I/O", "io_light": "I/O-light"})
    agg["ack_ktps"] = agg["ack_tps"] / 1000.0

    palette = {"Dep frontier LSN": "#7c3aed", "TideWAL": "#059669"}
    fig, axes = plt.subplots(1, 3, figsize=(8.4, 3.0))
    specs = [
        ("ack_ktps", "Ack [K tx/s]", "Throughput"),
        ("atomic_per_tx", "Atomic ops / tx", "Global atomics"),
        ("lsn_alloc_ns_per_tx", "ns / tx", "LSN allocation"),
    ]
    for ax, (metric, ylabel, title) in zip(axes, specs):
        sns.barplot(
            data=agg.sort_values("condition"),
            x="condition_label",
            y=metric,
            hue="mode_label",
            palette=palette,
            ax=ax,
            width=0.72,
            errorbar=None,
        )
        ax.set_xlabel("")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        if ax is not axes[-1]:
            ax.legend_.remove()
        else:
            ax.legend(title="", loc="upper right")
    fig.suptitle("Cstamp-as-logical-LSN ablation", y=1.05)
    save(fig, out)


def plot_overhead_breakdown(out):
    df = pd.read_csv(FAIR_CSV)
    df = df[(df["system"] == "TideWAL") & (df["total_threads"] == 32)].copy()
    df = df.sort_values("workload")
    components = [
        ("frontier_collect_ns_per_tx", "frontier collect", "#2563eb"),
        ("frontier_publish_ns_per_tx", "frontier publish", "#f59e0b"),
        ("fdatasync_ns_per_tx", "fdatasync", "#10b981"),
    ]
    workloads = df["workload"].tolist()
    x = range(len(workloads))
    bottoms = [0.0] * len(workloads)

    fig, ax = plt.subplots(figsize=(5.8, 3.4))
    for col, label, color in components:
        vals = (df[col] / 1000.0).tolist()
        ax.bar(x, vals, bottom=bottoms, label=label, color=color, width=0.62)
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    ax.set_xticks(list(x), workloads)
    ax.set_ylabel("Time per tx [us]")
    ax.set_title("TideWAL overhead breakdown at 32 total active threads")
    ax.legend(title="", ncols=3, loc="upper right", bbox_to_anchor=(1.0, 1.18))
    save(fig, out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fig-dir", type=Path, default=FIG_DIR)
    args = parser.parse_args()
    set_style()
    plot_max_pending_pareto(args.fig_dir / "fig_extra_max_pending_pareto.pdf")
    plot_cstamp_lsn_ablation(args.fig_dir / "fig_extra_cstamp_lsn_ablation.pdf")
    plot_overhead_breakdown(args.fig_dir / "fig_extra_tidewal_overhead_breakdown.pdf")


if __name__ == "__main__":
    main()
