#!/bin/bash
# ablation測定: ./run_ablation.sh <出力ファイル> <実験ID>
# E2: tmpfs (pwal/ayame x th 8,32,96)
# E3a/E3b: Pareto flush間隔スイープ (pwal / ayame, 96th, ntx 1,5,10,50,200)
# E4a/E4b: Zipf x rmw (pwal / ayame, 96th, rmw=true th 0,0.6,0.9,0.99 + 対照 rmw=false th0.9)
set -eu
OUT="$1"
EXP="$2"
THRESHOLD=2.0
WAIT_LIMIT_SEC=360
EXTIME=10
TRIALS=3
BUILD=/home/sa2shun/tx-playground/ccbench/build/cc
WALDIR_DISK=/home/sa2shun/tx-playground/ccbench/pwal_logs_bench
WALDIR_TMPFS=/dev/shm/pwal_ablation
mkdir -p "$WALDIR_DISK" "$WALDIR_TMPFS"

waited=0
while :; do
  load=$(cut -d' ' -f1 /proc/loadavg)
  ok=$(echo "$load < $THRESHOLD" | bc -l)
  if [ "$ok" = "1" ]; then break; fi
  if [ "$waited" -ge "$WAIT_LIMIT_SEC" ]; then
    echo "TIMEOUT exp=$EXP (last load: $load)" >> "$OUT"
    exit 1
  fi
  sleep 15
  waited=$((waited + 15))
done

run() { # sys th ntx zipf rmw waldir label
  local sys=$1 th=$2 ntx=$3 zipf=$4 rmw=$5 waldir=$6 label=$7
  local exe
  case $sys in
    ermia_pwal)  exe="$BUILD/ermia_pwal/ycsb_ermia_pwal.exe" ;;
    ermia_ayame) exe="$BUILD/ermia_ayame/ycsb_ermia_ayame.exe" ;;
  esac
  for trial in $(seq 1 $TRIALS); do
    rm -f "$waldir"/*.wal
    out=$("$exe" -thread_num=$th -extime=$EXTIME -ycsb_rratio=50 \
          -pwal_flush_ntx=$ntx -ycsb_zipf_skew=$zipf -ycsb_rmw=$rmw \
          -pwal_dir="$waldir" 2>/dev/null)
    g() { echo "$out" | grep "^$1" | cut -f2; }
    echo -e "$label\t$sys\ttrial=$trial\tth=$th\tntx=$ntx\tzipf=$zipf\trmw=$rmw\ttps=$(g 'throughput\[tps\]')\tabort=$(g 'abort_rate')\tp50=$(g 'pwal_lat_p50')\tp99=$(g 'pwal_lat_p99')\tflushw=$(g 'pwal_phase_flushwait')\tdepw=$(g 'pwal_phase_depwait')\tsync=$(g 'pwal_sync_avg')\tload=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
  done
}

echo "start: $(date +%H:%M:%S) exp=$EXP loadavg=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
case $EXP in
  E2)
    for th in 8 32 96; do
      run ermia_pwal  $th 10 0 false "$WALDIR_TMPFS" E2_tmpfs
      run ermia_ayame $th 10 0 false "$WALDIR_TMPFS" E2_tmpfs
    done ;;
  E3a) for n in 1 5 10 50 200; do run ermia_pwal  96 $n 0 false "$WALDIR_DISK" E3_pareto; done ;;
  E3b) for n in 1 5 10 50 200; do run ermia_ayame 96 $n 0 false "$WALDIR_DISK" E3_pareto; done ;;
  E4a)
    for z in 0 0.6 0.9 0.99; do run ermia_pwal 96 10 $z true "$WALDIR_DISK" E4_zipf; done
    run ermia_pwal 96 10 0.9 false "$WALDIR_DISK" E4_ctrl ;;
  E4b)
    for z in 0 0.6 0.9 0.99; do run ermia_ayame 96 10 $z true "$WALDIR_DISK" E4_zipf; done
    run ermia_ayame 96 10 0.9 false "$WALDIR_DISK" E4_ctrl ;;
  E5a|E5b)
    case $EXP in
      E5a) sys=ermia_pwal;  exe="$BUILD/ermia_pwal/ycsb_ermia_pwal.exe" ;;
      E5b) sys=ermia_ayame; exe="$BUILD/ermia_ayame/ycsb_ermia_ayame.exe" ;;
    esac
    runk() { # mode K label
      local mode=$1 K=$2 label=$3
      for trial in $(seq 1 $TRIALS); do
        rm -f "$WALDIR_DISK"/*.wal
        out=$("$exe" -thread_num=96 -extime=$EXTIME -ycsb_rratio=50 \
              -pwal_mode=$mode -pwal_backpressure=$K -pwal_dir="$WALDIR_DISK" 2>/dev/null)
        g() { echo "$out" | grep "^$1" | cut -f2; }
        echo -e "$label\t$sys\ttrial=$trial\tth=96\tmode=$mode\tK=$K\ttps=$(g 'throughput\[tps\]')\tavg=$(g 'pwal_lat_avg')\tp50=$(g 'pwal_lat_p50')\tp99=$(g 'pwal_lat_p99')\tflushw=$(g 'pwal_phase_flushwait')\tdepw=$(g 'pwal_phase_depwait')\tsync=$(g 'pwal_sync_avg')\tload=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
      done
    }
    runk self 0 E5_kself
    for K in 8 32 128 512 2048 0; do runk pipeline $K E5_ksweep; done ;;
  *) echo "unknown exp: $EXP" >> "$OUT"; exit 1 ;;
esac
echo "end: $(date +%H:%M:%S) exp=$EXP loadavg=$(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
