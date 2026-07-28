#!/bin/bash
# Kxスレッドのマトリクス測定: ./run_kthread.sh <出力> <sys> <K>
set -eu
OUT="$1"; SYS="$2"; K="$3"
THRESHOLD=2.0; WAIT_LIMIT_SEC=360; EXTIME=10; TRIALS=3
WALDIR=/home/sa2shun/tx-playground/ccbench/pwal_logs_bench
BUILD=/home/sa2shun/tx-playground/ccbench/build/cc
case $SYS in
  ermia_pwal)  exe="$BUILD/ermia_pwal/ycsb_ermia_pwal.exe" ;;
  ermia_ayame) exe="$BUILD/ermia_ayame/ycsb_ermia_ayame.exe" ;;
esac
waited=0
while :; do
  load=$(cut -d' ' -f1 /proc/loadavg)
  [ "$(echo "$load < $THRESHOLD" | bc -l)" = "1" ] && break
  [ "$waited" -ge "$WAIT_LIMIT_SEC" ] && { echo "TIMEOUT $SYS K=$K (load $load)" >> "$OUT"; exit 1; }
  sleep 15; waited=$((waited+15))
done
echo "start: $(date +%H:%M:%S) $SYS K=$K loadavg=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
for th in 1 2 4 8 16 32 64 96; do
  for trial in 1 2 3; do
    rm -f "$WALDIR"/*.wal
    out=$("$exe" -thread_num=$th -extime=$EXTIME -ycsb_rratio=50 -pwal_mode=pipeline \
          -pwal_backpressure=$K -pwal_dir="$WALDIR" 2>/dev/null)
    g() { echo "$out" | grep "^$1" | cut -f2; }
    echo -e "E6_kth\t$SYS\ttrial=$trial\tth=$th\tK=$K\ttps=$(g 'throughput\[tps\]')\tsamples=$(g 'pwal_lat_samples')\tp50=$(g 'pwal_lat_p50')\tp99=$(g 'pwal_lat_p99')\tload=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
  done
done
echo "end: $(date +%H:%M:%S) $SYS K=$K" >> "$OUT"
