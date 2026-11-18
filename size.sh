#!/usr/bin/env bash
# size.sh — handler-size axis on Intel, cut to ~2 h; ordered by priority.
# Env: POINTS, RUNS (process runs per cell, default 3), REC and K (recording block for the online part).

set -uo pipefail
cd "$(dirname "$0")"
. ./topo.sh

POINTS=${POINTS:-"cpptrader tx c32 c128 d64 big"}
RUNS=${RUNS:-3}
REC=${REC:-online/L1-1829.pkt}
K=${K:-35}

blk() { echo "=== $(date +%H:%M) $*"; }
pad() { head -c $(($1 * 700)) /dev/zero | tr '\0' x; }
r() { local name=$1; shift; timeout 40m ./run.sh "$name" "$@" > /dev/null 2>&1 || echo "!! failed: $name"; echo "$(date +%H:%M) $name"; }
has() { [ "$1" = cpptrader ] || [ "$1" = null ] || [ -x "build/bench-$1" ]; }
lead() { case "$1" in c128|big|c512) echo 400 ;; *) echo 200 ;; esac; }

A_CONDS="--conds hot,none,shadow,prewake,pfwake --dt-ms 10 --period-us 1000 --warm-lead-us 20 --pf dc --trials 1000"
B_CONDS="--conds hot,sys,spin,spinshadow,spinpf --dt-ms 10 --period-us 50 --trials 1000"

# Figure data from the series before this one, while the stand is idle.
rm -f results/online/export.csv
python3 export.py results/online/L1-A results/online/L1-B results/online/NZ-A results/online/NZ-A-aa \
  results/online/NZ-B results/online/NZ-B-aa results/online/NZ-B-tick > export.log 2>&1 || echo "!! export.py"
./setup-tx.sh > /dev/null 2>&1 || echo "!! setup-tx.sh"

blk "N: measurement window control (null handler)"
./cstate.sh 0 > /dev/null; HANDLER=null r szN-B $B_CONDS --no-counters
./cstate.sh 8 > /dev/null; HANDLER=null r szN-A $A_CONDS --prewake-us 200 --no-counters

blk "B: spin loop (POLL), T = 50 us"
./cstate.sh 0 > /dev/null
for k in $(seq 1 "$RUNS"); do
  for p in $POINTS; do
    has "$p" || { echo "!! no build/bench-$p"; continue; }
    HANDLER=$p PAD=$(pad $k) r "szB-$p-r$k" $B_CONDS --seed $k --no-counters
  done
done
for p in $POINTS; do
  has "$p" && HANDLER=$p r "szB-$p-phase" --conds hot,spinshadow --dt-ms 10 --period-us 50 --event-phase random \
    --trials 2000 --seed 7 --no-counters
  has "$p" && HANDLER=$p PAD=$(pad 1) r "szB-$p-aa" $B_CONDS --seed 1 --no-counters
done

blk "A: sleep (all states)"
./cstate.sh 8 > /dev/null
for k in $(seq 1 "$RUNS"); do
  for p in $POINTS; do
    has "$p" || continue
    HANDLER=$p PAD=$(pad $k) r "szA-$p-r$k" $A_CONDS --prewake-us "$(lead "$p")" --seed $k --no-counters
  done
done

blk "P: p999, 20k trials per cell"
for p in cpptrader big; do
  ./cstate.sh 0 > /dev/null
  HANDLER=$p r "szP-$p-spin" --conds hot,spin,spinshadow --dt-ms 1 --period-us 50 --trials 20000 --seed 1 --no-counters
  HANDLER=$p r "szP-$p-phase" --conds spinshadow --dt-ms 1 --period-us 50 --event-phase random --trials 20000 --seed 2 \
    --no-counters
  ./cstate.sh 8 > /dev/null
  HANDLER=$p r "szP-$p-sleep" --conds hot,none,prewake --dt-ms 10 --prewake-us "$(lead "$p")" --trials 20000 --seed 1 \
    --no-counters
done

blk "T: shadow period in the spin loop"
./cstate.sh 0 > /dev/null
for p in tx c32 c128 big; do
  has "$p" || continue
  for T in 200 1000; do
    HANDLER=$p r "szT-$p-T$T" --conds hot,spin,spinshadow --dt-ms 10 --period-us $T --trials 1000 --seed 1 --no-counters
  done
done

blk "R: several warm-up passes in prewake"
./cstate.sh 8 > /dev/null
for p in c128 big; do
  has "$p" || continue
  for K_ in 1 2 4; do
    HANDLER=$p r "szR-$p-K$K_" --conds hot,none,prewake --dt-ms 10 --prewake-us 400 --warm-reps $K_ --trials 1000 \
      --seed 1 --no-counters
  done
done

blk "W: send warm-up W0 / W1 / W2"
for p in tx big; do
  has "$p" || continue
  for w in 0 1 2; do
    ./cstate.sh 0 > /dev/null
    COLDPATH_WARM_SEND=$w HANDLER=$p r "szW-$p-w$w-spin" --conds hot,spin,spinshadow --dt-ms 10 --period-us 1000 \
      --trials 1000 --seed 1 --no-counters
    ./cstate.sh 8 > /dev/null
    COLDPATH_WARM_SEND=$w HANDLER=$p r "szW-$p-w$w-sleep" --conds hot,none,prewake --dt-ms 10 --prewake-us 400 \
      --trials 1000 --seed 1 --no-counters
  done
done

blk "K: counters (b — data, c — code)"
for g in b c; do
  for p in cpptrader c128 big; do
    kern=(); [ "$p" = big ] && kern=(--count-kernel)
    ./cstate.sh 0 > /dev/null
    HANDLER=$p r "szK-$p-B-$g" --conds hot,spin,spinshadow,spinpf --dt-ms 10 --period-us 50 --trials 300 --group $g \
      "${kern[@]}"
    ./cstate.sh 8 > /dev/null
    HANDLER=$p r "szK-$p-A-$g" --conds hot,none,prewake,pfwake --dt-ms 10 --prewake-us "$(lead "$p")" --pf dc \
      --trials 300 --group $g "${kern[@]}"
  done
done
./cstate.sh 9 > /dev/null

blk "Online stand: big in architecture A, send warm-up W2, 5-minute blocks"
for row in "spin spinshadow@50 spinshadow@1000 spinpf@50" "spinpf@50 spin spinshadow@50 spinshadow@1000" \
           "spinshadow@1000 spinpf@50 spin spinshadow@50" "spinshadow@50 spinshadow@1000 spinpf@50 spin"; do
  LIMIT_S=300 COLDPATH_WARM_SEND=2 RECV=recv-big ./online.sh replay SZ-big "$REC" "$K" all "$row" 50
done
python3 export.py results/online/SZ-big >> export.log 2>&1

blk "done"
touch size.done
