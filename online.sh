#!/usr/bin/env bash
# online.sh — online bench runs, in blocks.
#
# Feed:
#   ./online.sh feed live NAME [feed args]   — live Bitstamp feed in the background on core 1, starts paused
#   ./online.sh feed synth NAME [feed args]  — S0: empty packets every Δt (receivers run with --synth)
#   ./online.sh feed stop                    — stop the connector
# Blocks (connector already running):
#   ./online.sh block SERIES GROUP "m2 m3 m4 m5" [T_us] [policies "p2 p3 p4 p5"] [seconds]
#       m — wait mode on cores 2, 3, 4, 5 (sleep spin spinshadow sleepshadow spinpf; "-" leaves the core empty);
#       T — shadow period (spinshadow: 50; sleepshadow uses 2×T unless SLEEP_T is set); mode@T sets it per core;
#       mode+nb also runs the background load NB (nop, mem:256k, mem:64m, code:128k) on that core;
#       policies — cstate.sh number per core (default 8, all allowed).
#   ./online.sh square SERIES GROUP "a b c d" [T] — Latin square: 4 blocks, modes rotated across cores.
# Replay a recording block by block (no connector needed):
#   ./online.sh replay SERIES RECORD.pkt BLOCK GROUP "m2 m3 m4 m5" [T] [policies]   (LIMIT_S: first N seconds only)
# Groups: all — full feed (architecture A), one — quiet pair only (architecture B).
# Env: BLOCK_S (600), WATCH (ltcbtc), SOLO (ltcbtc), SYNTH=1 — receivers in S0 mode, RECV (recv) — receiver build,
#   NB (mem:256k) — load of the +nb neighbours.

set -uo pipefail
cd "$(dirname "$0")"

BLOCK_S=${BLOCK_S:-600}
WATCH=${WATCH:-ltcbtc}
SOLO=${SOLO:-ltcbtc}
OUT=results/online
mkdir -p "$OUT" online
FEED_PID=online/feed.pid

group_ip() { case "$1" in all) echo 239.192.0.1 ;; one) echo 239.192.0.2 ;; *) echo "$1" ;; esac; }
log() { echo "[online] $(date +%H:%M:%S) $*"; }

idle_snap() { # $1: output file; idle-state counters of cores 1–5 and /proc/interrupts
  local c s
  { for c in 1 2 3 4 5; do for s in /sys/devices/system/cpu/cpu$c/cpuidle/state*; do
      echo "cpu$c $(cat $s/name) disable=$(cat $s/disable) usage=$(cat $s/usage) time_us=$(cat $s/time)"; done; done
    echo "--- interrupts"; cat /proc/interrupts; } > "$1" 2>/dev/null
}

feed() {
  local kind=$1; shift
  if [ "$kind" = stop ]; then
    [ -f $FEED_PID ] && kill -TERM "$(cat $FEED_PID)" 2>/dev/null; sleep 2; rm -f $FEED_PID; return
  fi
  local name=$1; shift
  [ -f $FEED_PID ] && kill -0 "$(cat $FEED_PID)" 2>/dev/null && { echo "connector already running"; return 1; }
  local args=(--out "online/$name.pkt" --start-paused)
  [ "$kind" = live ] && args+=(--solo "$SOLO")
  systemd-run --quiet --scope --slice=online -p AllowedCPUs=1 \
    build/feed "$kind" "${args[@]}" "$@" >> "online/$name.log" 2>&1 &
  sleep 1
  pgrep -f "feed(\.py)? $kind --out online/$name.pkt" | head -1 > $FEED_PID
  log "connector $kind started, pid $(cat $FEED_PID), log online/$name.log"
  [ "$kind" = live ] && { log "waiting 20 s for order book snapshots"; sleep 20; }
}

# start_receivers DIR GROUP "m2 m3 m4 m5" T "p2 p3 p4 p5" — start receivers and wait until they are ready
start_receivers() {
  local dir=$1 gip; gip=$(group_ip "$2"); local -a modes=($3) pols=($5); local T=$4
  local c=2 i=0 m p t nb  # m must be local, or it clobbers array m in square() (bash dynamic scoping)
  rm -f "$dir"/*.ready
  for m in "${modes[@]}"; do
    if [ "$m" != - ]; then
      p=${pols[$i]:-8}; t=$T; nb=0
      case "$m" in *+nb) nb=1; m=${m%+nb} ;; esac
      [ "$m" = sleepshadow ] && t=${SLEEP_T:-$((2 * T))}
      local name=$m
      case "$m" in *@*) t=${m#*@}; m=${m%@*}; name=${m}T$t ;; esac
      [ $nb = 1 ] && name=${name}nb
      CPU=$c ./cstate.sh "$p" > /dev/null
      local extra=(); [ "${SYNTH:-0}" = 1 ] && extra=(--synth)
      systemd-run --quiet --scope --slice=online -p AllowedCPUs=$c \
        "build/${RECV:-recv}" --mode "$m" --cpu $c --watch "$WATCH" --group "$gip" --period-us "$t" "${extra[@]}" \
        --out "$dir/cpu$c-$name.rec" --ready "$dir/cpu$c.ready" 2> "$dir/cpu$c-$name.log" &
      echo "cpu$c $name T=$t policy=$p recv=${RECV:-recv}" >> "$dir/layout.txt"
      if [ $nb = 1 ]; then # SCHED_OTHER neighbour; starts once the receiver is ready, stopped by stop_neighbours
        local load=${NB:-mem:256k}
        systemd-run --quiet --scope --slice=online -p AllowedCPUs=$c build/neighbour --load "${load/code:128k/code}" \
          --cpu $c --wait-file "$dir/cpu$c.ready" --log "$dir/cpu$c-nb.log" > "$dir/cpu$c-nb.out" 2>&1 &
        echo "cpu$c neighbour NB=$load" >> "$dir/layout.txt"
      fi
    fi
    c=$((c + 1)); i=$((i + 1))
  done
  local want=0; for m in "${modes[@]}"; do [ "$m" != - ] && want=$((want + 1)); done
  for _ in $(seq 100); do [ "$(ls "$dir"/*.ready 2>/dev/null | wc -l)" -ge $want ] && break; sleep 0.1; done
  sleep 1  # TSC calibration and multicast join
}

stop_neighbours() { pkill -TERM -x neighbour 2>/dev/null && sleep 0.5; return 0; }

wait_receivers() { # receivers exit on the end-of-block packet; SIGTERM any still running after 15 s
  for _ in $(seq 150); do pgrep -x "${RECV:-recv}" > /dev/null || return 0; sleep 0.1; done
  log "receivers did not exit on their own — SIGTERM"; pkill -TERM -x recv; sleep 1
}

block() { # SERIES GROUP "m2 m3 m4 m5" [T] [policies] [seconds]
  local series=$1 grp=$2 modes=$3 T=${4:-50} pols=${5:-"8 8 8 8"} secs=${6:-$BLOCK_S} c
  [ -f $FEED_PID ] && kill -0 "$(cat $FEED_PID)" 2>/dev/null || { echo "connector not running"; return 1; }
  local dir="$OUT/$series/$(date +%m%d-%H%M%S)"
  mkdir -p "$dir"
  echo "group=$grp modes=$modes T=$T policies=$pols secs=$secs watch=$WATCH synth=${SYNTH:-0}" > "$dir/layout.txt"
  start_receivers "$dir" "$grp" "$modes" "$T" "$pols"
  idle_snap "$dir/idle-before.txt"
  kill -USR2 "$(cat $FEED_PID)"
  log "block $dir: $modes, T=$T, policies $pols, $secs s"
  sleep "$secs"
  kill -USR1 "$(cat $FEED_PID)"
  wait_receivers
  stop_neighbours
  idle_snap "$dir/idle-after.txt"
  for c in 2 3 4 5; do CPU=$c ./cstate.sh 8 > /dev/null; done
  tail -q -n1 "$dir"/cpu*.log 2>/dev/null
}

square() { # SERIES GROUP "a b c d" [T] — four blocks; in block k, mode i runs on core 2 + (i + k) mod 4
  local series=$1 grp=$2 T=${4:-50} k c; local -a m=($3)
  for k in 0 1 2 3; do
    local row=()
    for c in 0 1 2 3; do row+=("${m[$(((c - k + 4) % 4))]}"); done
    block "$series" "$grp" "${row[*]}" "$T" || return 1
  done
}

replay() { # SERIES RECORD BLOCK GROUP "m2 m3 m4 m5" [T] [policies]
  local series=$1 rec=$2 b=$3 grp=$4 modes=$5 T=${6:-50} pols=${7:-"8 8 8 8"} c
  local dir="$OUT/$series/$(date +%m%d-%H%M%S)"
  mkdir -p "$dir"
  echo "group=$grp modes=$modes T=$T policies=$pols replay=$rec block=$b limit_s=${LIMIT_S:-0} watch=$WATCH" > "$dir/layout.txt"
  start_receivers "$dir" "$grp" "$modes" "$T" "$pols"
  idle_snap "$dir/idle-before.txt"
  log "replay $rec block $b: $modes, T=$T, policies $pols"
  systemd-run --quiet --scope --slice=online -p AllowedCPUs=1 \
    build/feed replay "$rec" --block "$b" ${LIMIT_S:+--limit-s "$LIMIT_S"} >> "$dir/feed.log" 2>&1
  wait_receivers
  stop_neighbours
  idle_snap "$dir/idle-after.txt"
  for c in 2 3 4 5; do CPU=$c ./cstate.sh 8 > /dev/null; done
  tail -q -n1 "$dir"/cpu*.log "$dir/feed.log" 2>/dev/null
}

cmd=${1:?"usage: see the header of online.sh"}; shift
case "$cmd" in
  feed) feed "$@" ;;
  block) block "$@" ;;
  square) square "$@" ;;
  replay) replay "$@" ;;
  *) echo "unknown command $cmd"; exit 2 ;;
esac
