#!/usr/bin/env python3
import csv
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


THREADS = [1, 2, 4, 8, 16, 32]
PROTOCOLS = ["rc_ssn", "rc_ssn_pwal", "rc_ssn_pwal_d_v1", "rc_ssn_pwal_d_v2"]
SCENARIOS = {
    "base": {"rratio": 50, "skew": 0},
    "read_heavy": {"rratio": 90, "skew": 0},
    "write_heavy": {"rratio": 10, "skew": 0},
    "skewed": {"rratio": 50, "skew": 0.9},
}


def parse_metric(text, name):
    match = re.search(rf"^{re.escape(name)}:\s*([^\s]+)", text, re.MULTILINE)
    return match.group(1) if match else ""


def tail_text(path, n=22):
    return "\n".join(path.read_text(errors="replace").splitlines()[-n:])


def run_one(root, log_dir, wal_dir, scenario, proto, thread_num, cfg):
    exe = root / "build" / "cc" / proto / f"ycsb_{proto}.exe"
    out = log_dir / f"{scenario}_{proto}_{thread_num}.out"
    err = log_dir / f"{scenario}_{proto}_{thread_num}.err"
    cmd = [
        str(exe),
        "--extime=10",
        f"--thread_num={thread_num}",
        "--ycsb_tuple_num=1000",
        "--ycsb_max_ope=10",
        f"--ycsb_rratio={cfg['rratio']}",
        f"--ycsb_zipf_skew={cfg['skew']}",
    ]
    env = dict(os.environ)
    env["CCBENCH_WAL_DIR"] = str(wal_dir)
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.run(cmd, cwd=root, stdout=stdout, stderr=stderr, env=env)

    stdout_text = out.read_text(errors="replace")
    return {
        "scenario": scenario,
        "protocol": proto,
        "thread_num": thread_num,
        "rratio": cfg["rratio"],
        "skew": cfg["skew"],
        "exit_code": proc.returncode,
        "actual_extime": parse_metric(stdout_text, "actual_extime"),
        "commit_counts": parse_metric(stdout_text, "commit_counts_"),
        "abort_counts": parse_metric(stdout_text, "abort_counts_"),
        "maxrss_kb": parse_metric(stdout_text, "maxrss").replace(" kB", ""),
        "abort_rate": parse_metric(stdout_text, "abort_rate"),
        "latency_ns": parse_metric(stdout_text, "latency[ns]"),
        "throughput_tps": parse_metric(stdout_text, "throughput[tps]"),
        "throughput_ops": parse_metric(stdout_text, "throughput[ops]"),
        "stdout_log": str(out),
        "stderr_log": str(err),
    }, out, err


def main():
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = root / "results" / f"ycsb_ssn_pwal_d_matrix_{stamp}.txt"
    log_dir = root / "results" / f"ycsb_ssn_pwal_d_logs_{stamp}"
    wal_dir = root / "results" / f"ycsb_ssn_pwal_d_wal_{stamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    wal_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    with result.open("w") as output:
        print("YCSB SSN-PWAL-D comparison", file=output)
        print(f"date: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", file=output)
        print("protocols: " + " ".join(PROTOCOLS), file=output)
        print("rc_ssn_pwal: global durable-prefix wait", file=output)
        print("rc_ssn_pwal_d_v1: direct predecessor frontier wait", file=output)
        print("rc_ssn_pwal_d_v2: transitive worker-wise depVector wait", file=output)
        print("threads: " + " ".join(map(str, THREADS)), file=output)
        print("extime: 10", file=output)
        print("fixed: ycsb_tuple_num=1000 ycsb_max_ope=10", file=output)
        print("wal_dir: " + str(wal_dir), file=output)
        print("scenarios:", file=output)
        for name, cfg in SCENARIOS.items():
            print(f"  {name}: rratio={cfg['rratio']} skew={cfg['skew']}", file=output)

        for scenario, cfg in SCENARIOS.items():
            for thread_num in THREADS:
                for proto in PROTOCOLS:
                    print(f"RUN {scenario} {proto} thread={thread_num}", flush=True)
                    row, out, err = run_one(
                        root, log_dir, wal_dir, scenario, proto, thread_num, cfg)
                    rows.append(row)
                    print(
                        f"\n===== scenario={scenario} protocol={proto} thread_num={thread_num} "
                        f"rratio={cfg['rratio']} skew={cfg['skew']} =====",
                        file=output,
                    )
                    print("command_exit_code:\t" + str(row["exit_code"]), file=output)
                    print("stdout_log:\t" + str(out), file=output)
                    print("stderr_log:\t" + str(err), file=output)
                    print("stdout_tail:", file=output)
                    print(tail_text(out), file=output)
                    err_tail = tail_text(err, 10)
                    if err_tail:
                        print("stderr_tail:", file=output)
                        print(err_tail, file=output)
                    output.flush()

        print("\n===== SUMMARY_CSV =====", file=output)
        fields = [
            "scenario", "protocol", "thread_num", "rratio", "skew", "exit_code",
            "actual_extime", "commit_counts", "abort_counts", "maxrss_kb",
            "abort_rate", "latency_ns", "throughput_tps", "throughput_ops",
            "stdout_log", "stderr_log",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(result)


if __name__ == "__main__":
    main()
