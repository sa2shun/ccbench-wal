#!/bin/bash
# 耐久スループット取り直し(quick: 5s x 1試行) ./run_durable.sh <out> <chunk>
set -eu
OUT="$1"; CHUNK="$2"
EXTIME=5
WALDIR=/home/sa2shun/tx-playground/ccbench/pwal_logs_bench
BUILD=/home/sa2shun/tx-playground/ccbench/build/cc
run() { # sys mode K N th
  local sys=$1 mode=$2 K=$3 N=$4 th=$5 exe
  case $sys in
    ermia_pwal)  exe="$BUILD/ermia_pwal/ycsb_ermia_pwal.exe" ;;
    ermia_ayame) exe="$BUILD/ermia_ayame/ycsb_ermia_ayame.exe" ;;
  esac
  rm -f "$WALDIR"/*.wal
  out=$(timeout 90 "$exe" -thread_num=$th -extime=$EXTIME -ycsb_rratio=50 \
        -pwal_mode=$mode -pwal_backpressure=$K -pwal_flush_ntx=$N -pwal_dir="$WALDIR" 2>/dev/null)
  g() { echo "$out" | grep "^$1" | cut -f2; }
  echo -e "$sys\tmode=$mode\tK=$K\tN=$N\tth=$th\ttps=$(g 'throughput\[tps\]')\tsamples=$(g 'pwal_lat_samples')\tp50=$(g 'pwal_lat_p50')\tload=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
}
echo "start $CHUNK $(date +%H:%M:%S) load=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
case $CHUNK in
  A)
    for th in 1 2 4 8 16 32 64 96; do run ermia_pwal self 0 10 $th; done
    for n in 1 5 10 50 200 1000 5000 20000 100000; do run ermia_pwal self 0 $n 96; done ;;
  B)
    for K in 1 8 32 128; do for th in 1 2 4 8 16 32 64 96; do run ermia_ayame pipeline $K 10 $th; done; done ;;
  C)
    for K in 512 2048 0; do for th in 1 2 4 8 16 32 64 96; do run ermia_ayame pipeline $K 10 $th; done; done ;;
esac
echo "end $CHUNK $(date +%H:%M:%S)" >> "$OUT"
