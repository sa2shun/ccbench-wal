#!/bin/bash
# 正式測定: 1チャンク = 1ワークロード x 3系統 x スレッド数スイープ x extime秒
# 使い方: ./run_formal.sh <出力ファイル> <rratio> <trial番号>
# loadavgが閾値未満になるまで待ってから実行する(CLAUDE.mdルール1)
set -eu
OUT="$1"
RRATIO="$2"
TRIAL="$3"
THRESHOLD=2.0
WAIT_LIMIT_SEC=360
EXTIME=10
WALDIR=/home/sa2shun/tx-playground/ccbench/pwal_logs_bench
BUILD=/home/sa2shun/tx-playground/ccbench/build/cc
mkdir -p "$WALDIR"

waited=0
while :; do
  load=$(cut -d' ' -f1 /proc/loadavg)
  ok=$(echo "$load < $THRESHOLD" | bc -l)
  if [ "$ok" = "1" ]; then break; fi
  if [ "$waited" -ge "$WAIT_LIMIT_SEC" ]; then
    echo "TIMEOUT rratio=$RRATIO trial=$TRIAL (last load: $load)" >> "$OUT"
    exit 1
  fi
  sleep 15
  waited=$((waited + 15))
done

echo "start: $(date +%H:%M:%S) rratio=$RRATIO trial=$TRIAL loadavg=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
for th in 1 2 4 8 16 32 64 96; do
  for sys in ermia ermia_pwal ermia_ayame; do
    rm -f "$WALDIR"/*.wal
    case $sys in
      ermia)       exe="$BUILD/ermia/ycsb_ermia.exe"; extra="" ;;
      ermia_pwal)  exe="$BUILD/ermia_pwal/ycsb_ermia_pwal.exe"; extra="-pwal_dir=$WALDIR" ;;
      ermia_ayame) exe="$BUILD/ermia_ayame/ycsb_ermia_ayame.exe"; extra="-pwal_dir=$WALDIR" ;;
    esac
    out=$("$exe" -thread_num=$th -extime=$EXTIME -ycsb_rratio=$RRATIO $extra 2>/dev/null)
    g() { echo "$out" | grep "^$1" | cut -f2; }
    echo -e "$sys\ttrial=$TRIAL\trratio=$RRATIO\tth=$th\ttps=$(g 'throughput\[tps\]')\tavg=$(g 'pwal_lat_avg')\tp50=$(g 'pwal_lat_p50')\tp95=$(g 'pwal_lat_p95')\tp99=$(g 'pwal_lat_p99')\texec=$(g 'pwal_phase_exec')\tflushw=$(g 'pwal_phase_flushwait')\tdepw=$(g 'pwal_phase_depwait')\tsync=$(g 'pwal_sync_avg')\tload=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
  done
done
echo "end: $(date +%H:%M:%S) rratio=$RRATIO trial=$TRIAL loadavg=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
