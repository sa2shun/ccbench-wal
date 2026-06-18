#!/usr/bin/env python3
"""Ayame architecture figure (worker/flusher/committer pipeline).

Draws the Ayame commit pipeline for Section 3: workers execute and validate
transactions, assign the SSN commit timestamp (reused as the logical LSN),
build a commit record carrying the closed dependency frontier F, and enqueue
it to their own WAL shard without flushing or waiting.  Flushers batch each
shard's records and call fdatasync once per batch, advancing the durable
vector D.  The committer returns a durable acknowledgment to the client only
once F <= D, i.e. once the transaction's own record and all the records it
depends on are durable.  The schematic is hand-laid-out (no measured data).
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper" / "figures" / "fig_ayame_architecture.pdf"

INK = "#1f2937"
BLUE = "#1d4ed8"
GRAY = "#4b5563"
AMBER = "#d97706"
GREEN = "#047857"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "pdf.fonttype": 42, "ps.fonttype": 42,
    "figure.facecolor": "white", "savefig.facecolor": "white",
})


def box(ax, x, y, w, h, edge, fill, lw=1.8, rounding=0.06):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0.0,rounding_size={rounding}",
        linewidth=lw, edgecolor=edge, facecolor=fill, mutation_aspect=1.0, zorder=2))


def arrow(ax, p0, p1, color=INK, lw=1.8, rad=0.0, style="-|>"):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle=style, mutation_scale=16, linewidth=lw,
        color=color, connectionstyle=f"arc3,rad={rad}", zorder=3))


def main():
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    ax.set_xlim(0, 13.1)
    ax.set_ylim(0, 6)
    ax.axis("off")

    # --- Workers ---
    box(ax, 0.3, 1.4, 2.7, 3.2, BLUE, "#eff6ff")
    ax.text(1.65, 4.25, "Workers", ha="center", va="center",
            fontsize=12.5, fontweight="bold", color=BLUE)
    for i, t in enumerate([
        "execute + SSN validate",
        r"assign cstamp $T.c$",
        r"build $\langle T.c,\, F\rangle$",
        "enqueue (no flush)",
    ]):
        ax.text(1.65, 3.55 - i * 0.5, t, ha="center", va="center",
                fontsize=8.7, color=INK)

    # --- WAL shards ---
    for i in range(3):
        yy = 3.55 - i * 0.95
        box(ax, 4.0, yy, 2.0, 0.72, GRAY, "#f3f4f6", lw=1.4, rounding=0.04)
        ax.text(5.0, yy + 0.36, f"WAL shard {i+1}", ha="center", va="center",
                fontsize=8.6, color=GRAY)
    ax.text(5.0, 4.95, "append-only\nlog streams", ha="center", va="center",
            fontsize=8.4, color=GRAY, style="italic")

    # --- Flushers ---
    box(ax, 6.95, 1.55, 2.0, 3.0, AMBER, "#fffbeb")
    ax.text(7.95, 4.2, "Flushers", ha="center", va="center",
            fontsize=12.5, fontweight="bold", color=AMBER)
    for i, t in enumerate(["batch records", r"one \texttt{fdatasync}", r"advance $D[i]$"]):
        ax.text(7.95, 3.5 - i * 0.55, t.replace(r"\texttt{", "").replace("}", ""),
                ha="center", va="center", fontsize=8.7, color=INK)

    # --- Durable vector D (shared) ---
    box(ax, 9.5, 4.95, 2.1, 0.75, INK, "#e5e7eb", lw=1.4, rounding=0.05)
    ax.text(10.55, 5.32, r"durable vector $D$", ha="center", va="center",
            fontsize=9.0, color=INK)

    # --- Committer ---
    box(ax, 9.5, 1.5, 2.1, 2.9, GREEN, "#ecfdf5")
    ax.text(10.55, 4.05, "Committer", ha="center", va="center",
            fontsize=12.5, fontweight="bold", color=GREEN)
    ax.text(10.55, 3.35, r"ack iff", ha="center", va="center",
            fontsize=9.2, color=INK)
    ax.text(10.55, 2.85, r"$F \leq D$", ha="center", va="center",
            fontsize=14, fontweight="bold", color=GREEN)
    ax.text(10.55, 2.2, "(own record +\ndependencies\ndurable)", ha="center",
            va="center", fontsize=8.0, color=INK, style="italic")

    # --- Arrows ---
    arrow(ax, (3.05, 3.0), (3.95, 3.0), color=INK)          # workers -> shards
    ax.text(3.5, 3.35, r"enqueue", ha="center", va="bottom", fontsize=8.3, color=INK)
    arrow(ax, (6.05, 3.0), (6.9, 3.0), color=INK)           # shards -> flushers
    arrow(ax, (8.55, 4.55), (9.95, 5.0), color=AMBER, rad=0.15)  # flushers -> D
    arrow(ax, (10.55, 4.9), (10.55, 4.45), color=INK)       # D -> committer
    # register frontier: workers -> committer (top curved)
    arrow(ax, (2.0, 4.65), (9.7, 4.35), color=BLUE, rad=-0.22, lw=1.5)
    ax.text(5.9, 5.75, r"register $(F, T)$", ha="center", va="center",
            fontsize=8.6, color=BLUE)
    # durable ack out
    arrow(ax, (11.62, 2.6), (12.55, 2.6), color=GREEN)
    ax.text(12.35, 2.2, "durable\nack", ha="center", va="top",
            fontsize=8.4, color=GREEN)

    fig.tight_layout(pad=0.2)
    fig.savefig(OUT, bbox_inches="tight")
    plt.close(fig)
    print("FIG:", OUT)


if __name__ == "__main__":
    main()
