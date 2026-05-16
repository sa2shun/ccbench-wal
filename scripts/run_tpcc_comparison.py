#!/usr/bin/env python3
import csv
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


THREADS = [1, 2, 4, 8, 16, 32]
PROTOCOLS = ["rc", "ermia"]
SCENARIOS = {
    "default": {"payment": 43, "order_status": 4, "delivery": 4, "stock_level": 4},
    "payment_heavy": {"payment": 80, "order_status": 4, "delivery": 4, "stock_level": 4},
    "neworder_heavy": {"payment": 10, "order_status": 4, "delivery": 4, "stock_level": 4},
}


def parse_metric(text, name):
    m = re.search(rf"^{re.escape(name)}:\s*([^\s]+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def tail_text(path, n=24):
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-n:])


def run_one(root, log_dir, scenario, proto, thread_num, mix):
    exe = root / "build" / "cc" / proto / f"tpcc_{proto}.exe"
    out = log_dir / f"{scenario}_{proto}_{thread_num}.out"
    err = log_dir / f"{scenario}_{proto}_{thread_num}.err"
    cmd = [
        str(exe),
        "--extime=10",
        "--gc_inter_us=100000000",
        f"--thread_num={thread_num}",
        "--tpcc_num_wh=1",
        f"--tpcc_perc_payment={mix['payment']}",
        f"--tpcc_perc_order_status={mix['order_status']}",
        f"--tpcc_perc_delivery={mix['delivery']}",
        f"--tpcc_perc_stock_level={mix['stock_level']}",
    ]
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, cwd=root, stdout=stdout, stderr=stderr)

    stdout_text = out.read_text(errors="replace")
    row = {
        "scenario": scenario,
        "protocol": proto,
        "thread_num": thread_num,
        "tpcc_num_wh": 1,
        "payment": mix["payment"],
        "order_status": mix["order_status"],
        "delivery": mix["delivery"],
        "stock_level": mix["stock_level"],
        "exit_code": proc.returncode,
        "actual_extime": parse_metric(stdout_text, "actual_extime"),
        "commit_counts": parse_metric(stdout_text, "commit_counts_"),
        "abort_counts": parse_metric(stdout_text, "abort_counts_"),
        "maxrss_kb": parse_metric(stdout_text, "maxrss").replace(" kB", ""),
        "abort_rate": parse_metric(stdout_text, "abort_rate"),
        "latency_ns": parse_metric(stdout_text, "latency[ns]"),
        "throughput_tps": parse_metric(stdout_text, "throughput[tps]"),
    }
    return row, out, err


def main():
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = root / "results" / f"tpcc_rc_vs_ermia_wh1_mix_{stamp}.txt"
    log_dir = root / "results" / f"tpcc_logs_{stamp}"
    log_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    with result.open("w") as f:
        print("TPC-C RC vs ERMIA comparison", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("note: compact result; full stdout/stderr are in " + str(log_dir), file=f)
        print("protocols: " + " ".join(PROTOCOLS), file=f)
        print("threads: " + " ".join(map(str, THREADS)), file=f)
        print("extime: 10", file=f)
        print("fixed: tpcc_num_wh=1 gc_inter_us=100000000", file=f)
        print("scenarios:", file=f)
        for name, mix in SCENARIOS.items():
            new_order = 100 - mix["payment"] - mix["order_status"] - mix["delivery"] - mix["stock_level"]
            print(
                f"  {name}: payment={mix['payment']} order_status={mix['order_status']} "
                f"delivery={mix['delivery']} stock_level={mix['stock_level']} new_order={new_order}",
                file=f,
            )

        for scenario, mix in SCENARIOS.items():
            for thread_num in THREADS:
                for proto in PROTOCOLS:
                    print(f"RUN {scenario} {proto} thread={thread_num}", flush=True)
                    row, out, err = run_one(root, log_dir, scenario, proto, thread_num, mix)
                    rows.append(row)
                    print(
                        f"\n===== scenario={scenario} protocol={proto} thread_num={thread_num} "
                        f"wh=1 payment={mix['payment']} order_status={mix['order_status']} "
                        f"delivery={mix['delivery']} stock_level={mix['stock_level']} =====",
                        file=f,
                    )
                    print("command_exit_code:\t" + str(row["exit_code"]), file=f)
                    print("stdout_log:\t" + str(out), file=f)
                    print("stderr_log:\t" + str(err), file=f)
                    print("stdout_tail:", file=f)
                    print(tail_text(out), file=f)
                    err_tail = tail_text(err, 12)
                    if err_tail:
                        print("stderr_tail:", file=f)
                        print(err_tail, file=f)
                    f.flush()

        print("\n===== SUMMARY_CSV =====", file=f)
        fieldnames = [
            "scenario",
            "protocol",
            "thread_num",
            "tpcc_num_wh",
            "payment",
            "order_status",
            "delivery",
            "stock_level",
            "exit_code",
            "actual_extime",
            "commit_counts",
            "abort_counts",
            "maxrss_kb",
            "abort_rate",
            "latency_ns",
            "throughput_tps",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
