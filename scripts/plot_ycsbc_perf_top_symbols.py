#!/usr/bin/env python3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "paper" / "figures"
TABLE_DIR = ROOT / "paper" / "tables"
OUT_FIG = FIG_DIR / "fig_ycsbc_perf_top_symbols_horizontal.pdf"
OUT_CSV = TABLE_DIR / "ycsbc_perf_top_symbols_selected_20260609.csv"

ROWS = [
    {"system": "P-WAL", "symbol": "TxExecutor::read", "self_pct": 58.88},
    {"system": "P-WAL", "symbol": "TxExecutor::ssn_parallel_commit", "self_pct": 23.03},
    {"system": "P-WAL", "symbol": "MasstreeWrapper::get_value", "self_pct": 3.75},
    {"system": "P-WAL", "symbol": "TxExecutor::mainte", "self_pct": 2.99},
    {"system": "P-WAL", "symbol": "TxExecutor::read_internal", "self_pct": 2.83},
    {"system": "TideWAL", "symbol": "TxExecutor::read", "self_pct": 52.88},
    {"system": "TideWAL", "symbol": "TxExecutor::ssn_parallel_commit", "self_pct": 20.92},
    {"system": "TideWAL", "symbol": "pthread_mutex_lock", "self_pct": 3.37},
    {"system": "TideWAL", "symbol": "TxExecutor::read_internal", "self_pct": 3.02},
    {"system": "TideWAL", "symbol": "TxExecutor::mergeVersionFrontier", "self_pct": 2.67},
]

COLORS = {"P-WAL": "#d97706", "TideWAL": "#047857"}


def setup_style():
    sns.set_theme(
        context="paper",
        style="whitegrid",
        font_scale=1.08,
        rc={
            "font.family": "DejaVu Sans",
            "axes.edgecolor": "#d1d5db",
            "axes.linewidth": 0.9,
            "grid.color": "#e5e7eb",
            "grid.linewidth": 0.85,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        },
    )
    plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white"})


def draw():
    setup_style()
    df = pd.DataFrame(ROWS)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 3.9), sharex=True, constrained_layout=True)
    for ax, system in zip(axes, ["P-WAL", "TideWAL"]):
        sub = df[df["system"] == system].iloc[::-1]
        ax.barh(
            sub["symbol"],
            sub["self_pct"],
            color=COLORS[system],
            alpha=0.92,
            height=0.62,
        )
        for y, value in enumerate(sub["self_pct"]):
            ax.text(
                value + 1.0,
                y,
                f"{value:.2f}%",
                va="center",
                ha="left",
                fontsize=9.5,
                color="#111827",
            )
        ax.set_title(system, fontsize=12.5, fontweight="bold", pad=8)
        ax.set_xlabel("Self samples [%]")
        ax.set_xlim(0, 65)
        ax.grid(True, axis="x")
        ax.grid(False, axis="y")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#d1d5db")
        ax.spines["bottom"].set_color("#d1d5db")
        ax.tick_params(axis="y", length=0, pad=5)
    fig.text(
        0.01,
        1.03,
        "YCSB-C perf top symbols at 32 worker threads",
        ha="left",
        va="bottom",
        fontsize=11.0,
        color="#374151",
    )
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FIG, bbox_inches="tight")
    plt.close(fig)
    print(OUT_CSV)
    print(OUT_FIG)


if __name__ == "__main__":
    draw()
