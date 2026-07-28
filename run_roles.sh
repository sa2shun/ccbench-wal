#!/bin/bash
# F掃引の丁寧版: ./run_roles.sh <out> <trial>  (順序を試行ごとに反転して系統誤差を相殺)
set -eu
OUT="$1"; TRIAL="$2"
THRESHOLD=2.0; WAIT_LIMIT_SEC=360; EXTIME=10
WALDIR=/home/sa2shun/tx-playground/ccbench/pwal_logs_bench
EXE=/home/sa2shun/tx-playground/ccbench/build/cc/ermia_ayame/ycsb_ermia_ayame.exe
waited=0
while :; do
  load=$(cut -d' ' -f1 /proc/loadavg)
  [ "$(echo "$load < $THRESHOLD" | bc -l)" = "1" ] && break
  [ "$waited" -ge "$WAIT_LIMIT_SEC" ] && { echo "TIMEOUT trial=$TRIAL (load $load)" >> "$OUT"; exit 1; }
  sleep 15; waited=$((waited+15))
done
echo "start trial=$TRIAL $(date +%H:%M:%S) load=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
FS="2 4 8 16 32"
[ "$TRIAL" = "2" ] && FS="32 16 8 4 2"
for F in $FS; do
  W=$((96 - F - 1))
  for K in 32 512 2048; do
    rm -f "$WALDIR"/*.wal
    out=$(timeout 90 "$EXE" -thread_num=$W -extime=$EXTIME -ycsb_rratio=50 -pwal_mode=pipeline \
          -pwal_backpressure=$K -pwal_flushers=$F -pwal_dir="$WALDIR" 2>/dev/null)
    s=$(echo "$out"|grep samples|cut -f2); p=$(echo "$out"|grep p50|cut -f2)
    echo -e "trial=$TRIAL\tW=$W\tF=$F\tK=$K\tdurable=$((s/EXTIME))\tp50=$p\tload=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
  done
done
echo "end trial=$TRIAL $(date +%H:%M:%S)" >> "$OUT"
