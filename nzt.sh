#!/usr/bin/env bash
# nzt.sh — tails of the spin-loop penalty with and without the timer tick, in one nohz_full boot:
# B — no tick; T — tick brought back on core 5 by a frequency-mode perf event. Order B, T, T, B against drift.

cd "$(dirname "$0")"

. ./topo.sh
./cstate.sh 0 > /dev/null

loc() { grep "LOC:" /proc/interrupts | awk -v c=$CPU '{print $(c + 2)}'; }

run() { # NAME TICK ARGS...
  local name=$1 tick=$2; shift 2
  local a b pid=""
  [ "$tick" = T ] && { perf record -F 1 -C "$CPU" -e cycles -o /tmp/nzt.data -- sleep 3600 > /dev/null 2>&1 & pid=$!; sleep 2; }
  a=$(loc)
  SKIP_ANALYZE=1 ./run.sh "$name" "$@" > /dev/null 2>&1
  b=$(loc)
  [ -n "$pid" ] && { kill $pid; wait $pid 2>/dev/null; }
  echo "$(date +%H:%M) $name LOC core $CPU: $((b - a))"
}

run nztB-10 B --conds hot,spin,spinshadow --dt-ms 10 --period-us 50 --trials 20000 --seed 1 --no-counters
run nztT-10 T --conds hot,spin,spinshadow --dt-ms 10 --period-us 50 --trials 20000 --seed 1 --no-counters
run nztT-100 T --conds hot,spin --dt-ms 100 --trials 5000 --seed 2 --no-counters
run nztB-100 B --conds hot,spin --dt-ms 100 --trials 5000 --seed 2 --no-counters

./cstate.sh 9 > /dev/null
touch nzt.done
