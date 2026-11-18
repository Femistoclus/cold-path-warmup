#!/usr/bin/env bash
# series.sh — main series: null-handler calibration, then idle policies POLL, up to C1, default, up to C3.
# Time from runs without counters: 5 process runs (2 for C3) with different seed and environment size,
# i.e. different memory layout (Mytkowicz 2009); one separate counter run per policy, for attribution only.

set -uo pipefail
cd "$(dirname "$0")"

ARGS="--conds hot,cold,sys,spin,spinshadow,none,empty,shadow,prewake --dt-ms 1,10,100 --trials 300"

pad() { head -c $(($1 * 700)) /dev/zero | tr '\0' x; } # $1 × 700 bytes, to vary environment size

./cstate.sh 8
HANDLER=null ./run.sh null $ARGS --no-counters

for pol in 0 1 8 3; do
  ./cstate.sh $pol
  runs=5
  [ $pol = 3 ] && runs=2
  for r in $(seq 1 $runs); do
    PAD=$(pad $r) ./run.sh "s$pol-r$r" $ARGS --seed $r --no-counters
  done
  ./run.sh "s$pol-cnt" $ARGS --seed 11
done

./cstate.sh 8
