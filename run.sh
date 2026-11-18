#!/usr/bin/env bash
# One run: build → environment snapshot → measurement on core CPU → metrics.
#   ./run.sh NAME [bench args], e.g.:
#   ./run.sh quick --trials 100      # quick check, ~1 min
#   ./run.sh mainA --seed 1          # main run, ~6 min
#   ITCH_UNTIL=11:30 ./run.sh late   # order book at 11:30 instead of 10:00
# Env: HANDLER=cpptrader|null|synth, ITCH_FILE, ITCH_UNTIL.
#   ISO=ded|os|os-irq|l3|shared, NB=nop|mem:256k|mem:64m|code:128k|mix — isolation level and neighbour (iso.sh);
#   the level is undone when the run ends, also on failure.
# Results: results/<time>-<name>/{calls.csv, env.txt, bench.log, summary.txt, plot.png}

set -euo pipefail
cd "$(dirname "$0")"
. "$(dirname "$0")/topo.sh"
. "$(dirname "$0")/build.sh"

NAME=${1:?"usage: ./run.sh NAME [bench args]"}
shift
DIR="results/$(date +%m%d-%H%M%S)-$NAME"
mkdir -p "$DIR"

build
BENCH=$(bench_bin)
./env.sh > "$DIR/env.txt"
echo "bench: $BENCH, args: $*" >> "$DIR/env.txt"
echo "== $DIR"

# Idle-state counters and interrupts before/after: the diff shows which C-states the core actually entered.
idle() { for s in /sys/devices/system/cpu/cpu$CPU/cpuidle/state*; do
  echo "$(cat $s/name) disable=$(cat $s/disable) usage=$(cat $s/usage) time_us=$(cat $s/time)"; done; }

# Isolation level: the neighbour starts now but works only after bench creates nb.ready (order book loaded).
NBARGS=()
if [ -n "${ISO:-}" ]; then
  trap './iso.sh stop > /dev/null' EXIT
  ISO_DIR="$DIR" ./iso.sh "$ISO" ${NB:+"$NB"} > "$DIR/iso.txt" || { cat "$DIR/iso.txt"; exit 1; }
  grep '^isolation:' "$DIR/iso.txt" >> "$DIR/env.txt" || true
  grep -v '^isolation:' "$DIR/iso.txt" || true
  NBARGS+=(--ready "$DIR/nb.ready")
  case "$ISO" in l3 | shared) NBARGS+=(--neighbour-counter /dev/shm/coldpath-nb.cnt) ;; esac
fi

idle > "$DIR/idle-before.txt" 2>/dev/null || true
cat /proc/interrupts > "$DIR/irq-before.txt" 2>/dev/null || true

# setup.sh keeps all slices off core CPU, so bench runs in its own slice allowed only that core.
# Falls back to taskset if systemd lacks AllowedCPUs.
if [ -n "$(systemctl show -p AllowedCPUs --value user.slice 2>/dev/null)" ]; then
  systemd-run --quiet --scope --slice=bench -p AllowedCPUs="$CPU" \
    "$BENCH" --cpu "$CPU" --out "$DIR/calls.csv" ${NBARGS[@]+"${NBARGS[@]}"} "$@" 2>&1 | tee "$DIR/bench.log"
else
  echo "(systemd without AllowedCPUs — running via taskset)"
  taskset -c "$CPU" "$BENCH" --cpu "$CPU" --out "$DIR/calls.csv" ${NBARGS[@]+"${NBARGS[@]}"} "$@" 2>&1 |
    tee "$DIR/bench.log"
fi

idle > "$DIR/idle-after.txt" 2>/dev/null || true
cat /proc/interrupts > "$DIR/irq-after.txt" 2>/dev/null || true
if [ -n "${ISO:-}" ]; then ./iso.sh stop; fi   # after the snapshots: an idle core would add C-state entries

[ "${SKIP_ANALYZE:-0}" = 1 ] || python3 analyze.py "$DIR/calls.csv" | tee "$DIR/summary.txt"
