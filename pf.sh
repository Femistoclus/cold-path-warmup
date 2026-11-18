#!/usr/bin/env bash
# pf.sh — software prefetch series. A: time, idle policies up to C3 / default × prefetch set 0/d/c/dc × 5 process runs.
# B: rdpmc counters, same policies × sets × groups a–d. C: single-run sweeps (ε, δ, two passes, ahead-of-time only,
# Δt = 100 ms, POLL, spinpf period T).

set -uo pipefail
cd "$(dirname "$0")"

M="--conds hot,none,spin,spinshadow,spinpf,prewake,pfwake,pfevent"
P="--dt-ms 10 --warm-lead-us 20 --period-us 50"

pad() { head -c $(($1 * 700)) /dev/zero | tr '\0' x; }

# A
for pol in 3 8; do
  ./cstate.sh $pol
  for set in 0 d c dc; do
    for r in 1 2 3 4 5; do
      PAD=$(pad $r) ./run.sh "pA$pol-$set-r$r" $M $P --pf $set --seed $r --trials 1000 --no-counters
    done
  done
done

# B
for pol in 3 8; do
  ./cstate.sh $pol
  for set in 0 d c dc; do
    for g in a b c d; do
      ./run.sh "pB$pol-$set-$g" $M $P --pf $set --trials 300 --group $g
    done
  done
done

# C
N="--trials 300 --no-counters"
for pol in 3 8; do
  ./cstate.sh $pol
  for e in 2 200; do ./run.sh "pC$pol-eps$e" $M --dt-ms 10 --period-us 50 --warm-lead-us $e --pf dc $N; done
  ./run.sh "pC$pol-delta150" $M $P --prewake-us 150 --pf dc $N
  ./run.sh "pC$pol-reps2" $M $P --pf-reps 2 --pf dc $N
  ./run.sh "pC$pol-g" $M $P --pf g $N
  ./run.sh "pC$pol-dt100" $M --dt-ms 100 --warm-lead-us 20 --period-us 50 --pf dc $N
done

./cstate.sh 0
for r in 1 2; do PAD=$(pad $r) ./run.sh "pC0-r$r" $M $P --pf dc --seed $r $N; done
for T in 10 20 100 300; do ./run.sh "pC0-T$T" --conds hot,spin,spinshadow,spinpf --dt-ms 10 --period-us $T --pf dc $N; done
./run.sh "pC0-T50-c" --conds hot,spin,spinshadow,spinpf --dt-ms 10 --period-us 50 --pf c $N
./run.sh "pC0-T50-d" --conds hot,spin,spinshadow,spinpf --dt-ms 10 --period-us 50 --pf d $N

./cstate.sh 8
