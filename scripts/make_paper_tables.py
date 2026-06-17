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


def tex_table(path, caption, label, headers, rows, align=None, footnote=None, wide=False):
    if align is None:
        align = "l" + "r" * (len(headers) - 1)
    env = "table*" if wide else "table"
    lines = [
        "\\begin{" + env + "}[t]",
        "  \\centering",
        "  \\caption{" + caption + "}",
        "  \\label{" + label + "}",
        "  \\small",
    ]
    if wide:
        # Span both columns and tighten spacing so wide tables are not clipped.
        lines.append("  \\setlength{\\tabcolsep}{6pt}")
    lines.append("  \\begin{tabular}{" + align + "}")
    lines.append("    \\hline")
    lines.append("    " + " & ".join(headers) + " \\\\")
    lines.append("    \\hline")
    for row in rows:
        lines.append("    " + " & ".join(row) + " \\\\")
    lines.extend([
        "    \\hline",
        "  \\end{tabular}",
    ])
    if footnote:
        lines.append("  \\\\[-1mm]")
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
    for r in y32:
        rows.append([
            str(r["workload"]),
            str(r["system"]),
            k_tps(r["ack_tps"]),
            whole(r["p99_us"]),
            whole(r["pending"]),
            whole(r["fdatasync_count"]),
            one(r["commits_per_fdatasync"]),
            one(r["frontier_bytes_per_tx"]),
            two(r["global_atomic_per_tx"]),
        ])
    tex_table(
        TABLE_DIR / "table_ycsbabc_48worker_summary.tex",
        "End-to-end YCSB results at 48 transaction worker threads.",
        "tab:ycsbabc-48worker-summary",
        [
            "Workload",
            "System",
            "Ack tps",
            "p99 $\\mu$s",
            "Pending",
            "fdatasync calls",
            "Tx/sync",
            "Frontier B/tx",
            "WAL atomic/tx",
        ],
        rows,
        align="llrrrrrrr",
        footnote=(
            "Ack tps is durable-acknowledgment throughput. "
            "Ayame uses 9 flusher threads and 1 committer thread at 48 workers."
        ),
        wide=True,
    )

    rows = []
    for workload in ["YCSB-A", "YCSB-B", "YCSB-C"]:
        pwal = next(r for r in y32 if r["workload"] == workload and r["system"] == "P-WAL")
        tide = next(r for r in y32 if r["workload"] == workload and r["system"] == "Ayame")
        rows.append([
            workload,
            k_tps(pwal["ack_tps"]),
            k_tps(tide["ack_tps"]),
            f"{tide['ack_tps'] / pwal['ack_tps']:.2f}$\\times$",
            whole(tide["p99_us"]),
            whole(tide["pending"]),
        ])
    tex_table(
        TABLE_DIR / "table_ayame_speedup_48worker.tex",
        "Ayame throughput relative to P-WAL at 48 transaction worker threads.",
        "tab:ayame-speedup-48worker",
        ["Workload", "P-WAL tps", "Ayame tps", "Speedup", "Ayame p99 $\\mu$s", "Ayame pending"],
        rows,
        align="lrrrrr",
        wide=True,
    )

    perf = read_csv(TABLE_DIR / "ayame_perf_stat_20260609.csv")
    perf.sort(key=lambda r: (order_workloads[r["workload"]], order_systems[r["system"]]))
    rows = []
    for r in perf:
        rows.append([
            str(r["workload"]),
            str(r["system"]),
            k_tps(r["durable_ack_tps"]),
            one(r["cpu_util_cores"]),
            whole(r["cycles_per_tx"]),
            whole(r["instructions_per_tx"]),
            two(r["ipc"]),
            whole(r["context_switches"]),
            one(r["commits_per_fdatasync"]),
        ])
    tex_table(
        TABLE_DIR / "table_perf_stat_48worker.tex",
        "Perf-stat summary at 48 transaction worker threads.",
        "tab:perf-stat-48worker",
        ["Workload", "System", "Ack tps", "CPU cores", "Cycles/tx", "Instr/tx", "IPC", "Ctx sw.", "Tx/sync"],
        rows,
        align="llrrrrrrr",
        wide=True,
    )

    scaling = read_csv(TABLE_DIR / "ycsbb_perf_scaling_20260609.csv")
    scaling_mean = group_mean(scaling, ["system", "worker_threads"])
    rows = []
    for workers in [1, 12, 24, 36, 48, 60, 72, 84, 96]:
        vals = {r["system"]: r for r in scaling_mean if int(r["worker_threads"]) == workers}
        rows.append([
            str(workers),
            k_tps(vals["Single WAL"]["ack_tps"]),
            k_tps(vals["P-WAL"]["ack_tps"]),
            k_tps(vals["Ayame"]["ack_tps"]),
            one(vals["Single WAL"]["cpu_cores"]),
            one(vals["P-WAL"]["cpu_cores"]),
            one(vals["Ayame"]["cpu_cores"]),
        ])
    tex_table(
        TABLE_DIR / "table_ycsbb_perf_scaling.tex",
        "YCSB-B perf-stat worker scaling.",
        "tab:ycsbb-perf-scaling",
        ["Workers", "Single tps", "P-WAL tps", "Ayame tps", "Single CPU", "P-WAL CPU", "Ayame CPU"],
        rows,
        align="rrrrrrr",
        wide=True,
    )

    top = read_csv(TABLE_DIR / "ayame_perf_top_20260609.csv")
    rows = []
    for system in ["Single WAL", "P-WAL", "Ayame"]:
        for r in top:
            if r["workload"] == "YCSB-C" and r["system"] == system and int(r["rank"]) <= 5:
                rows.append([system, str(int(r["rank"])), f"{r['self_pct']:.2f}", fmt_symbol(r["symbol"])])
    tex_table(
        TABLE_DIR / "table_ycsbc_perf_top.tex",
        "Top self-time symbols for YCSB-C at 48 transaction worker threads.",
        "tab:ycsbc-perf-top",
        ["System", "Rank", "Self \\%", "Symbol"],
        rows,
        align="llrl",
        footnote="YCSB-C is read-only; WAL bytes and fdatasync counts are zero for all systems.",
        wide=True,
    )

    abl_path = TABLE_DIR / "ack_policy_ablation_20260614.csv"
    if abl_path.exists():
        abl = read_csv(abl_path)
        abl32 = [r for r in abl if int(r["worker_threads"]) == 48]
        pol_order = {"Global-prefix": 0, "Ayame": 1}
        abl32.sort(key=lambda r: (order_workloads[r["workload"]],
                                  pol_order.get(r["policy"], 9)))
        pol_label = {"Global-prefix": "Global-prefix",
                     "Ayame": "Dep.\\ frontier (Ayame)"}
        rows = []
        for r in abl32:
            rows.append([
                str(r["workload"]),
                pol_label.get(r["policy"], str(r["policy"])),
                k_tps(r["ack_tps"]),
                whole(r["p99_us"]),
                whole(r["pending"]),
            ])
        tex_table(
            TABLE_DIR / "table_ack_policy_ablation.tex",
            "Acknowledgment-policy comparison at 48 worker threads.  Both policies "
            "use the same asynchronous pipeline and differ only in the "
            "acknowledgment condition.",
            "tab:ack-policy",
            ["Workload", "Ack policy", "Ack tps", "p99 $\\mu$s", "Pending"],
            rows,
            align="llrrr",
        )

    print(TABLE_DIR / "table_ycsbabc_48worker_summary.tex")
    print(TABLE_DIR / "table_ayame_speedup_48worker.tex")
    print(TABLE_DIR / "table_perf_stat_48worker.tex")
    print(TABLE_DIR / "table_ycsbb_perf_scaling.tex")
    print(TABLE_DIR / "table_ycsbc_perf_top.tex")


if __name__ == "__main__":
    main()
