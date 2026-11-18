#!/usr/bin/env bash
# iso-night.sh — isolation stage 1: isolation × warm-up on i7-8700, no CAT. Run as root after setup.sh.
#   ./iso-night.sh 0            # smoke: every level and load, 100 trials (~15 min)
#   ./iso-night.sh              # the night: series 1 23 4 5 6 7 8 (~6 h)
#   ./iso-night.sh 4 5          # selected series
# 1 neighbour alone, 2 min per load on core CPU and on CPU−1; 23 shared × 5 loads and ded (policy 0 and 8) in halves:
# first half of the cells forward, second half reversed; 4 os, os-irq; 5 l3 × mem:64m, mix; 6 sweeps on shared;
# 7 counters (groups a, c); 8 A/A repeat and the neighbour alone again.
# Run names i<series>-<level>-<load>-p<idle policy>-<kind>-r<N> are parsed by iso.py; "no" = no neighbour.
# Each run undoes its level (run.sh); on exit the level is stopped and all idle states are allowed again.

set -uo pipefail
cd "$(dirname "$0")"
. ./topo.sh
trap './iso.sh stop > /dev/null; ./cstate.sh 8 > /dev/null' EXIT

pad() { head -c $(($1 * 700)) /dev/zero | tr '\0' x; }

LOADS=${LOADS:-"nop mem:256k mem:64m code:128k mix"}
P="--dt-ms 10 --warm-lead-us 20 --prewake-us 200 --pf dc"
SLEEP=${SLEEP:-"--conds hot,none,empty,shadow,prewake,pfwake,pfevent"}
SPIN="--conds hot,spin,spinshadow,spinpf"
N=${N:-10000}   # trials per run; a cell is two runs (20 thousand), quantiles over their union
REPS23=${REPS23:-2}   # 1 — only the forward pass of series 2–3
SOLO_S=${SOLO_S:-120}   # seconds of the neighbour alone

# Bench arguments of a run kind. Shared core: only kinds without a wait loop (bench is SCHED_FIFO).
kind_args() {
  case $1 in
    sleep) echo "$SLEEP $P --period-us 100 --no-counters" ;;
    spin) echo "$SPIN $P --period-us 50 --no-counters" ;;
    np) echo "--conds hot,none,prewake $P --no-counters" ;;
    T20 | T1000) echo "--conds hot,none,shadow $P --period-us ${1#T} --no-counters" ;;
    d20 | d50) echo "--conds hot,none,prewake,pfwake $P --prewake-us ${1#d} --no-counters" ;;
    pfd | pfc) echo "--conds hot,none,pfwake,pfevent $P --pf ${1#pf} --no-counters" ;;
    wr2 | wr4) echo "--conds hot,none,prewake $P --warm-reps ${1#wr} --no-counters" ;;
    dt1 | dt100) echo "--conds hot,none,shadow,prewake,pfwake $P --period-us 100 --dt-ms ${1#dt} --no-counters" ;;
    phase) echo "--conds hot,none,shadow $P --period-us 100 --event-phase random --no-counters" ;;
    ga | gc) echo "$SLEEP $P --period-us 100 --group ${1#g}" ;;
    *) echo "!! unknown run kind $1" >&2; return 1 ;;
  esac
}

tag() { local t=${1//:/}; echo "${t/#-/no}"; }   # mem:256k → mem256k, "-" → no

# cell SERIES LEVEL LOAD POLICY KIND REP TRIALS — one run.sh run; LOAD "-" means no neighbour
cell() {
  local s=$1 lv=$2 load=$3 pol=$4 kind=$5 r=$6 n=$7 args nb=$3
  [ "$load" = - ] && nb=""
  args=$(kind_args "$kind") || return 1
  [ "$lv" = shared ] && [[ $kind == spin ]] && { echo "!! spin is not run on the shared core"; return 1; }
  ./cstate.sh "$pol" > /dev/null
  # shellcheck disable=SC2086
  PAD=$(pad "$r") ISO=$lv NB=$nb ./run.sh "i$s-${lv//-/}-$(tag "$load")-p$pol-$kind-r$r" $args --seed "$r" --trials "$n"
}

solo() { # SERIES LOAD CORE SECONDS — the neighbour alone, its speed is the reference for the loss
  local dir
  dir="results/$(date +%m%d-%H%M%S)-i$1-solo-$(tag "$2")-c$3"
  mkdir -p "$dir"
  echo "== $dir"
  ISO_DIR=$dir ./iso.sh solo "$2" "$3" "$4"
}

# Series 2 and 3 in halves: rep 1 of every cell forward, then rep 2 in reverse order.
cells23() {
  local l
  for l in $LOADS; do echo "2 shared $l 8 sleep"; done
  echo "3 ded - 0 sleep"; echo "3 ded - 0 spin"; echo "3 ded - 8 sleep"
}

s0() {
  local l
  for l in $LOADS; do solo 0 "$l" "$CPU" 10; done
  for l in $LOADS; do cell 0 shared "$l" 8 sleep 1 100; done
  cell 0 ded - 0 sleep 1 100; cell 0 ded - 0 spin 1 100; cell 0 ded - 8 sleep 1 100
  cell 0 os - 0 sleep 1 100; cell 0 os-irq - 0 sleep 1 100
  cell 0 l3 mem:64m 0 sleep 1 100; cell 0 l3 mix 0 spin 1 100
  cell 0 shared mem:256k 8 ga 1 100
}

s1() { local l c; for c in "$CPU" "$((CPU - 1))"; do for l in $LOADS; do solo 1 "$l" "$c" "$SOLO_S"; done; done; }

s23() {
  local -a cells c
  local i
  mapfile -t cells < <(cells23)
  for ((i = 0; i < ${#cells[@]}; i++)); do read -ra c <<< "${cells[$i]}"; cell "${c[@]}" 1 $N; done
  [ "$REPS23" -ge 2 ] || return 0
  for ((i = ${#cells[@]} - 1; i >= 0; i--)); do read -ra c <<< "${cells[$i]}"; cell "${c[@]}" 2 $N; done
}

s4() {
  local k r
  for k in sleep spin; do cell 4 os - 0 "$k" 1 5000; done
  for r in 1 2; do for k in sleep spin; do cell 4 os-irq - 0 "$k" "$r" $N; done; done
  cell 4 os - 8 np 1 2000; cell 4 os-irq - 8 np 1 2000
}

s5() {
  local k r
  for r in 1 2; do for k in sleep spin; do cell 5 l3 mem:64m 0 "$k" "$r" $N; done; done
  for k in sleep spin; do cell 5 l3 mix 0 "$k" 1 5000; done
}

s6() {
  local l k
  for l in mem:256k code:128k; do
    for k in T20 T1000 d20 d50 pfd pfc wr2 wr4 dt1 phase; do cell 6 shared "$l" 8 "$k" 1 2000; done
    cell 6 shared "$l" 8 dt100 1 1000
  done
}

s7() {
  local l g
  for g in ga gc; do
    for l in mem:256k code:128k mem:64m; do cell 7 shared "$l" 8 "$g" 1 1000; done
    cell 7 ded - 0 "$g" 1 1000
  done
}

s8() {
  local l
  cell 8 shared mem:256k 8 sleep 3 $N
  cell 8 ded - 0 sleep 3 $N; cell 8 ded - 0 spin 3 $N
  for l in $LOADS; do solo 8 "$l" "$CPU" "$SOLO_S"; done
}

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
. ./build.sh
build || { echo "!! build failed"; exit 1; }

[ $# -eq 0 ] && set -- 1 23 4 5 6 7 8
for s in "$@"; do
  case $s in
    0 | 1 | 23 | 4 | 5 | 6 | 7 | 8) echo "[iso-night] $(date +%H:%M:%S) series $s"; "s$s" ;;
    *) echo "unknown series $s (0 1 23 4 5 6 7 8)"; exit 1 ;;
  esac
done

python3 iso.py results | tee results/iso-summary.txt
