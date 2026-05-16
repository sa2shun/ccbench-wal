#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def load_summary(path: Path) -> pd.DataFrame:
    lines = path.read_text().splitlines()
    try:
        start = lines.index("===== SUMMARY_CSV =====") + 1
    except ValueError as exc:
        raise SystemExit("SUMMARY_CSV marker not found") from exc

    rows = "\n".join(lines[start:])
    from io import StringIO

    df = pd.read_csv(StringIO(rows))
    df["thread_num"] = df["thread_num"].astype(int)
    df["abort_percent"] = df["abort_rate"] * 100
    df["latency_us"] = df["latency_ns"] / 1000
    df["throughput_mtps"] = df["throughput_tps"] / 1_000_000
    return df


def plot_dashboard(df: pd.DataFrame, out_prefix: Path) -> None:
    sns.set_theme(style="whitegrid", context="talk")
    palette = {"rc": "#2563eb", "ermia": "#dc2626"}
    scenarios = ["base", "read_heavy", "write_heavy", "skewed"]
    metrics = [
        ("throughput_tps", "Throughput", "tx/s", lambda ax: ax.ticklabel_format(axis="y", style="sci", scilimits=(6, 6))),
        ("latency_us", "Latency", "us/tx", None),
        ("abort_percent", "Abort Rate", "%", None),
    ]

    fig, axes = plt.subplots(
        nrows=len(scenarios),
        ncols=len(metrics),
        figsize=(17, 14),
        sharex=True,
        constrained_layout=True,
    )
    fig.suptitle("YCSB: Read Committed vs ERMIA", fontsize=24, fontweight="bold")

    for r, scenario in enumerate(scenarios):
        sub = df[df["scenario"] == scenario]
        for c, (metric, title, ylabel, formatter) in enumerate(metrics):
            ax = axes[r, c]
            sns.lineplot(
                data=sub,
                x="thread_num",
                y=metric,
                hue="protocol",
                style="protocol",
                markers=True,
                dashes=False,
                linewidth=2.6,
                markersize=8,
                palette=palette,
                ax=ax,
                legend=(r == 0 and c == 0),
            )
            ax.set_xscale("log", base=2)
            ax.set_xticks([1, 2, 4, 8, 16, 32])
            ax.set_xticklabels([1, 2, 4, 8, 16, 32])
            ax.set_title(f"{scenario}: {title}", fontsize=14, fontweight="bold")
            ax.set_xlabel("threads" if r == len(scenarios) - 1 else "")
            ax.set_ylabel(ylabel)
            ax.grid(True, which="major", alpha=0.35)
            if formatter:
                formatter(ax)
            if not (r == 0 and c == 0):
                legend = ax.get_legend()
                if legend:
                    legend.remove()

    fig.savefig(out_prefix.with_suffix(".png"), dpi=180)
    fig.savefig(out_prefix.with_suffix(".pdf"))


def plot_speedup(df: pd.DataFrame, out_prefix: Path) -> None:
    pivot = df.pivot_table(
        index=["scenario", "thread_num"],
        columns="protocol",
        values="throughput_tps",
    ).reset_index()
    pivot["speedup"] = pivot["rc"] / pivot["ermia"]

    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(12, 6), constrained_layout=True)
    sns.barplot(
        data=pivot,
        x="thread_num",
        y="speedup",
        hue="scenario",
        ax=ax,
    )
    ax.axhline(1.0, color="#111827", linewidth=1.2, linestyle="--")
    ax.set_title("RC throughput speedup over ERMIA", fontsize=20, fontweight="bold")
    ax.set_xlabel("threads")
    ax.set_ylabel("RC / ERMIA throughput")
    ax.legend(title="scenario", ncols=2)

    for container in ax.containers:
        ax.bar_label(container, fmt="%.2fx", fontsize=8, padding=2)

    fig.savefig(out_prefix.with_name(out_prefix.name + "_speedup").with_suffix(".png"), dpi=180)
    fig.savefig(out_prefix.with_name(out_prefix.name + "_speedup").with_suffix(".pdf"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_txt", type=Path)
    parser.add_argument("--out-prefix", type=Path)
    args = parser.parse_args()

    out_prefix = args.out_prefix
    if out_prefix is None:
        out_prefix = args.result_txt.with_suffix("")

    df = load_summary(args.result_txt)
    plot_dashboard(df, out_prefix.with_name(out_prefix.name + "_mpl_dashboard"))
    plot_speedup(df, out_prefix.with_name(out_prefix.name + "_mpl"))


if __name__ == "__main__":
    main()
