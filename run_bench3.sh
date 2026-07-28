#!/bin/bash
# 中間測定: ermia / ermia_pwal / ermia_ayame を YCSB-A/B/C × スレッド数でスイープ
# 使い方: ./run_bench3.sh <出力ファイル> <rratio>
set -eu
OUT="$1"
RRATIO="$2"
THRESHOLD=2.0
WAIT_LIMIT_SEC=480
EXTIME=3
WALDIR=/home/sa2shun/tx-playground/ccbench/pwal_logs_bench
BUILD=/home/sa2shun/tx-playground/ccbench/build/cc
mkdir -p "$WALDIR"

waited=0
while :; do
  load=$(cut -d' ' -f1 /proc/loadavg)
  ok=$(echo "$load < $THRESHOLD" | bc -l)
  if [ "$ok" = "1" ]; then break; fi
  if [ "$waited" -ge "$WAIT_LIMIT_SEC" ]; then
    echo "TIMEOUT: load did not drop below $THRESHOLD (last: $load)" >> "$OUT"
    exit 1
  fi
  sleep 15
  waited=$((waited + 15))
done

echo "start: $(date) loadavg=$(cat /proc/loadavg)" >> "$OUT"
for rratio in $RRATIO; do
  for th in 1 2 4 8 16 32 64 96; do
    for sys in ermia ermia_pwal ermia_ayame; do
      rm -f "$WALDIR"/*.wal
      case $sys in
        ermia)       exe="$BUILD/ermia/ycsb_ermia.exe"; extra="" ;;
        ermia_pwal)  exe="$BUILD/ermia_pwal/ycsb_ermia_pwal.exe"; extra="-pwal_dir=$WALDIR" ;;
        ermia_ayame) exe="$BUILD/ermia_ayame/ycsb_ermia_ayame.exe"; extra="-pwal_dir=$WALDIR" ;;
      esac
      out=$("$exe" -thread_num=$th -extime=$EXTIME -ycsb_rratio=$rratio $extra 2>/dev/null)
      tps=$(echo "$out" | grep "^throughput\[tps\]" | cut -f2)
      p50=$(echo "$out" | grep "^pwal_lat_p50" | cut -f2)
      p95=$(echo "$out" | grep "^pwal_lat_p95" | cut -f2)
      p99=$(echo "$out" | grep "^pwal_lat_p99" | cut -f2)
      avg=$(echo "$out" | grep "^pwal_lat_avg" | cut -f2)
      echo -e "$sys\trratio=$rratio\tth=$th\ttps=$tps\tlat_avg=$avg\tp50=$p50\tp95=$p95\tp99=$p99\tload=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
    done
  done
done
echo "end: $(date) loadavg=$(cat /proc/loadavg)" >> "$OUT"
