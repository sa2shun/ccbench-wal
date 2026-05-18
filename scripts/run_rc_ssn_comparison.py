#!/usr/bin/env python3
import csv
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


THREADS = [1, 2, 4, 8, 16, 32]
PROTOCOLS = ["rc", "rc_ssn", "ermia"]

YCSB_SCENARIOS = {
    "base": {"rratio": 50, "skew": 0},
    "read_heavy": {"rratio": 90, "skew": 0},
    "write_heavy": {"rratio": 10, "skew": 0},
    "skewed": {"rratio": 50, "skew": 0.9},
}

TPCC_SCENARIOS = {
    "default": {"payment": 43, "order_status": 4, "delivery": 4, "stock_level": 4},
    "payment_heavy": {"payment": 80, "order_status": 4, "delivery": 4, "stock_level": 4},
    "neworder_heavy": {"payment": 10, "order_status": 4, "delivery": 4, "stock_level": 4},
}


def parse_metric(text, name):
    m = re.search(rf"^{re.escape(name)}:\s*([^\s]+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def tail_text(path, n=24):
    return "\n".join(path.read_text(errors="replace").splitlines()[-n:])


def run_cmd(root, log_dir, workload, scenario, proto, thread_num, extra_args):
    exe = root / "build" / "cc" / proto / f"{workload}_{proto}.exe"
    out = log_dir / f"{workload}_{scenario}_{proto}_{thread_num}.out"
    err = log_dir / f"{workload}_{scenario}_{proto}_{thread_num}.err"
    cmd = [str(exe), "--extime=10", f"--thread_num={thread_num}", *extra_args]
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, cwd=root, stdout=stdout, stderr=stderr)

    stdout_text = out.read_text(errors="replace")
    row = {
        "workload": workload,
        "scenario": scenario,
        "protocol": proto,
        "thread_num": thread_num,
        "exit_code": proc.returncode,
        "actual_extime": parse_metric(stdout_text, "actual_extime"),
        "commit_counts": parse_metric(stdout_text, "commit_counts_"),
        "abort_counts": parse_metric(stdout_text, "abort_counts_"),
        "maxrss_kb": parse_metric(stdout_text, "maxrss").replace(" kB", ""),
        "abort_rate": parse_metric(stdout_text, "abort_rate"),
        "latency_ns": parse_metric(stdout_text, "latency[ns]"),
        "throughput_tps": parse_metric(stdout_text, "throughput[tps]"),
        "stdout_log": str(out),
        "stderr_log": str(err),
    }
    return row, out, err


def write_run_block(f, row, out, err, params):
    print(
        f"\n===== workload={row['workload']} scenario={row['scenario']} "
        f"protocol={row['protocol']} thread_num={row['thread_num']} {params} =====",
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


def run_ycsb(root, result, log_dir):
    rows = []
    with result.open("w") as f:
        print("YCSB RC / RC_SSN / ERMIA comparison", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("protocols: " + " ".join(PROTOCOLS), file=f)
        print("threads: " + " ".join(map(str, THREADS)), file=f)
        print("extime: 10", file=f)
        print("fixed: ycsb_tuple_num=1000 ycsb_max_ope=10", file=f)
        print("scenarios:", file=f)
        for name, cfg in YCSB_SCENARIOS.items():
            print(f"  {name}: rratio={cfg['rratio']} skew={cfg['skew']}", file=f)

        for scenario, cfg in YCSB_SCENARIOS.items():
            for thread_num in THREADS:
                for proto in PROTOCOLS:
                    print(f"RUN ycsb {scenario} {proto} thread={thread_num}", flush=True)
                    args = [
                        "--ycsb_tuple_num=1000",
                        "--ycsb_max_ope=10",
                        f"--ycsb_rratio={cfg['rratio']}",
                        f"--ycsb_zipf_skew={cfg['skew']}",
                    ]
                    row, out, err = run_cmd(root, log_dir, "ycsb", scenario, proto, thread_num, args)
                    rows.append(row)
                    write_run_block(f, row, out, err, f"rratio={cfg['rratio']} skew={cfg['skew']}")
                    f.flush()

        write_summary(f, rows)
    return rows


def run_tpcc(root, result, log_dir):
    rows = []
    with result.open("w") as f:
        print("TPC-C RC / RC_SSN / ERMIA comparison", file=f)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=f)
        print("protocols: " + " ".join(PROTOCOLS), file=f)
        print("threads: " + " ".join(map(str, THREADS)), file=f)
        print("extime: 10", file=f)
        print("fixed: tpcc_num_wh=1 gc_inter_us=100000000", file=f)
        print("scenarios:", file=f)
        for name, mix in TPCC_SCENARIOS.items():
            new_order = 100 - mix["payment"] - mix["order_status"] - mix["delivery"] - mix["stock_level"]
            print(
                f"  {name}: payment={mix['payment']} order_status={mix['order_status']} "
                f"delivery={mix['delivery']} stock_level={mix['stock_level']} new_order={new_order}",
                file=f,
            )

        for scenario, mix in TPCC_SCENARIOS.items():
            for thread_num in THREADS:
                for proto in PROTOCOLS:
                    print(f"RUN tpcc {scenario} {proto} thread={thread_num}", flush=True)
                    args = [
                        "--gc_inter_us=100000000",
                        "--tpcc_num_wh=1",
                        f"--tpcc_perc_payment={mix['payment']}",
                        f"--tpcc_perc_order_status={mix['order_status']}",
                        f"--tpcc_perc_delivery={mix['delivery']}",
                        f"--tpcc_perc_stock_level={mix['stock_level']}",
                    ]
                    row, out, err = run_cmd(root, log_dir, "tpcc", scenario, proto, thread_num, args)
                    rows.append(row)
                    write_run_block(
                        f,
                        row,
                        out,
                        err,
                        f"wh=1 payment={mix['payment']} order_status={mix['order_status']} "
                        f"delivery={mix['delivery']} stock_level={mix['stock_level']}",
                    )
                    f.flush()

        write_summary(f, rows)
    return rows


def write_summary(f, rows):
    print("\n===== SUMMARY_CSV =====", file=f)
    fieldnames = [
        "workload",
        "scenario",
        "protocol",
        "thread_num",
        "exit_code",
        "actual_extime",
        "commit_counts",
        "abort_counts",
        "maxrss_kb",
        "abort_rate",
        "latency_ns",
        "throughput_tps",
        "stdout_log",
        "stderr_log",
    ]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def main():
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = root / "results" / f"rc_ssn_compare_logs_{stamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    ycsb_result = root / "results" / f"ycsb_rc_rcssn_ermia_matrix_{stamp}.txt"
    tpcc_result = root / "results" / f"tpcc_rc_rcssn_ermia_wh1_mix_{stamp}.txt"
    run_ycsb(root, ycsb_result, log_dir)
    run_tpcc(root, tpcc_result, log_dir)
    print(ycsb_result)
    print(tpcc_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
