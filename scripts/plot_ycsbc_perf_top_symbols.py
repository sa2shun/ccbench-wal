#!/usr/bin/env python3
import argparse
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

COLORS = {"P-WAL": "#d97706", "Ayame": "#047857"}


def setup_style():
    sns.set_theme(
        context="paper",
        style="whitegrid",
        font_scale=1.5,
        rc={
            "font.family": "DejaVu Sans",
            "axes.labelsize": 15,
            "axes.titlesize": 16,
            "xtick.labelsize": 13,
            "ytick.labelsize": 13,
            "axes.edgecolor": "#d1d5db",
            "axes.linewidth": 0.9,
            "grid.color": "#e5e7eb",
            "grid.linewidth": 0.85,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        },
    )
    plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white"})


def short_symbol(symbol):
    if symbol == "pthread_mutex_lock@@GLIBC_2.2.5":
        return "pthread_mutex_lock"
    if symbol.startswith("MasstreeWrapper<"):
        return "MasstreeWrapper::get_value"
    return symbol


def load_rows(input_csv, top_n):
    df = pd.read_csv(input_csv)
    legacy_name = "Tide" + "WAL"
    df["system"] = df["system"].replace({legacy_name: "Ayame"})
    df = df[(df["workload"] == "YCSB-C") & (df["system"].isin(["P-WAL", "Ayame"]))].copy()
    df["self_pct"] = pd.to_numeric(df["self_pct"], errors="coerce")
    df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
    df["symbol"] = df["symbol"].map(short_symbol)
    out = []
    for system in ["P-WAL", "Ayame"]:
        sub = df[df["system"] == system].sort_values("rank").head(top_n)
        out.append(sub[["system", "symbol", "self_pct"]])
    return pd.concat(out, ignore_index=True)


def draw(input_csv, top_n):
    setup_style()
    df = load_rows(input_csv, top_n)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 3.9), sharex=True, constrained_layout=True)
    for ax, system in zip(axes, ["P-WAL", "Ayame"]):
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
                fontsize=11,
                color="#111827",
            )
        ax.set_title(system, fontsize=15, fontweight="bold", pad=8)
        ax.set_xlabel("Self samples [%]")
        xmax = max(10.0, df["self_pct"].max() * 1.18)
        ax.set_xlim(0, xmax)
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
        "YCSB-C perf top symbols at 48 worker threads",
        ha="left",
        va="bottom",
        fontsize=13,
        color="#374151",
    )
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FIG, bbox_inches="tight")
    plt.close(fig)
    print(OUT_CSV)
    print(OUT_FIG)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-csv",
        default=str(TABLE_DIR / "ayame_perf_top_20260609.csv"),
    )
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()
    draw(Path(args.input_csv), args.top_n)
