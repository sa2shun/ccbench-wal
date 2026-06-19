#!/usr/bin/env python3
import csv
import re
from collections import defaultdict
from pathlib import Path


def fmt_symbol(sym):
    # Render C++ symbols in a monospace font with escaped underscores, and drop
    # template arguments (<Tuple>) so that < > do not become inverted marks
    # under the default font encoding.
    sym = re.sub(r"<[^>]*>", "", str(sym))
    return "\\texttt{" + sym.replace("_", "\\_") + "}"


ROOT = Path(__file__).resolve().parents[1]
TABLE_DIR = ROOT / "paper" / "tables"


def read_csv(path):
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for k, v in list(row.items()):
            if v is None or v == "":
                continue
            try:
                row[k] = float(v)
            except ValueError:
                pass
    return rows


def group_mean(rows, keys):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in keys)].append(row)
    out = []
    for key, items in groups.items():
        merged = {k: v for k, v in zip(keys, key)}
        numeric = {}
        for row in items:
            for k, v in row.items():
                if isinstance(v, float):
                    numeric.setdefault(k, []).append(v)
        for k, vals in numeric.items():
            merged[k] = sum(vals) / len(vals)
        out.append(merged)
    return out


def k_tps(v):
    return f"{v / 1000:.1f}K"


def whole(v):
    return f"{v:,.0f}"


def one(v):
    return f"{v:.1f}"


def two(v):
    return f"{v:.2f}"


def km(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1000:
        return f"{v / 1000:.0f}K"
    return f"{v:.0f}"


def bold(c):
    return "\\textbf{" + c + "}"


def tex_table(path, caption, label, headers, rows, align=None, footnote=None, wide=False,
              compact=False):
    if align is None:
        align = "l" + "r" * (len(headers) - 1)
    env = "table*" if wide else "table"
    place = "tb" if compact else "t"
    lines = [
        "\\begin{" + env + "}[" + place + "]",
        "  \\centering",
        "  \\caption{" + caption + "}",
        "  \\label{" + label + "}",
        "  \\footnotesize" if compact else "  \\small",
    ]
    if wide:
        # Span both columns and tighten spacing so wide tables are not clipped.
        lines.append("  \\setlength{\\tabcolsep}{6pt}")
    elif compact:
        # Keep a many-column table inside a single column so it flows inline.
        lines.append("  \\setlength{\\tabcolsep}{3pt}")
    lines.append("  \\begin{tabular}{" + align + "}")
    lines.append("    \\hline")
    lines.append("    " + " & ".join(headers) + " \\\\")
    lines.append("    \\hline")
    for row in rows:
        if isinstance(row, str):  # raw line such as \hline between groups
            lines.append("    " + row)
        else:
            lines.append("    " + " & ".join(row) + " \\\\")
    lines.extend([
        "    \\hline",
        "  \\end{tabular}",
    ])
    if footnote:
        # Paragraph break plus a small gap so the note sits clearly below the
        # bottom rule instead of overlapping the last row.
        lines.append("")
        lines.append("  \\vspace{1.5mm}")
        lines.append("  {\\footnotesize " + footnote + "}")
    lines.append("\\end{" + env + "}")
    path.write_text("\n".join(lines) + "\n")


def main():
    ycsb = read_csv(TABLE_DIR / "ycsbabc_ayame_worker_threads_20260609.csv")
    ycsb_mean = group_mean(ycsb, ["workload", "system", "worker_threads"])
    y32 = [r for r in ycsb_mean if int(r["worker_threads"]) == 48]
    order_workloads = {"YCSB-A": 0, "YCSB-B": 1, "YCSB-C": 2}
    order_systems = {"Single WAL": 0, "P-WAL": 1, "Ayame": 2}
    y32.sort(key=lambda r: (order_workloads[r["workload"]], order_systems[r["system"]]))

    rows = []
    for gi, workload in enumerate(["YCSB-A", "YCSB-B", "YCSB-C"]):
        group = [r for r in y32 if r["workload"] == workload]
        for idx, r in enumerate(group):
            cells = [
                k_tps(r["ack_tps"]),
                whole(r["p99_us"]),
                whole(r["pending"]),
                one(r["commits_per_fdatasync"]),
                two(r["global_atomic_per_tx"]),
            ]
            sysname = str(r["system"])
            if sysname == "Ayame":
                sysname = bold(sysname)
                cells = [bold(c) for c in cells]
            rows.append([workload if idx == 0 else "", sysname] + cells)
        if gi < 2:
            rows.append("\\hline")
    tex_table(
        TABLE_DIR / "table_ycsbabc_48worker_summary.tex",
        "End-to-end YCSB results at 48 transaction worker threads.  Arrows mark the "
        "better direction and the Ayame rows are in bold.",
        "tab:ycsbabc-48worker-summary",
        [
            "Workload",
            "System",
            "Ack tps $\\uparrow$",
            "p99 $\\mu$s $\\downarrow$",
            "Pending $\\downarrow$",
            "Tx/sync $\\uparrow$",
            "WAL atomic/tx $\\downarrow$",
        ],
        rows,
        align="llrrrrr",
        footnote=(
            "Ack tps is durable-acknowledgment throughput; Tx/sync is commits made "
            "durable per \\texttt{fdatasync}; WAL atomic/tx is per-transaction "
            "WAL-side global-counter accesses.  Ayame uses 9 flusher threads and "
            "1 committer at 48 workers."
        ),
        wide=True,
    )

    perf = read_csv(TABLE_DIR / "ayame_perf_stat_20260609.csv")
    perf_by = {(r["workload"], r["system"]): r for r in perf}
    rows = []
    for gi, workload in enumerate(["YCSB-A", "YCSB-B", "YCSB-C"]):
        for idx, system in enumerate(["Single WAL", "P-WAL", "Ayame"]):
            r = perf_by[(workload, system)]
            cells = [
                k_tps(r["durable_ack_tps"]),
                one(r["cpu_util_cores"]),
                km(r["cycles_per_tx"]),
                km(r["context_switches"]),
                one(r["commits_per_fdatasync"]),
            ]
            sysname = system
            if system == "Ayame":
                sysname = bold(sysname)
                cells = [bold(c) for c in cells]
            rows.append([workload if idx == 0 else "", sysname] + cells)
        if gi < 2:
            rows.append("\\hline")
    tex_table(
        TABLE_DIR / "table_perf_stat_48worker.tex",
        "Perf-stat summary at 48 transaction worker threads.  Cores is CPU-core "
        "utilization, Cyc/tx is CPU cycles per committed transaction, Ctx sw.\\ is "
        "total context switches, and Tx/sync is commits per \\texttt{fdatasync}.  "
        "Arrows mark the better direction and the Ayame rows are in bold.",
        "tab:perf-stat-48worker",
        ["Workload", "System", "Ack tps $\\uparrow$", "Cores", "Cyc/tx $\\downarrow$",
         "Ctx sw", "Tx/sync $\\uparrow$"],
        rows,
        align="llrrrrr",
        compact=True,
    )

    print(TABLE_DIR / "table_ycsbabc_48worker_summary.tex")
    print(TABLE_DIR / "table_perf_stat_48worker.tex")


if __name__ == "__main__":
    main()
