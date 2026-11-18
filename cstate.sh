#!/usr/bin/env bash
# cstate.sh N — allow C-states 0…N on the measurement core (CPU from topo.sh), disable the rest.
# i7-8700: 0 POLL, 1 C1, 2 C1E, 3 C3, 4 C6, 5 C7s, 6 C8, 7 C9, 8 C10; AMD usually 0 POLL, 1 C1, 2 C2 (CC6).
# "./cstate.sh 9" restores the default on any machine.

set -euo pipefail
. "$(dirname "$0")/topo.sh"

N=${1:?"usage: ./cstate.sh N"}

if ! compgen -G "/sys/devices/system/cpu/cpu$CPU/cpuidle/state*" > /dev/null; then
  echo "core $CPU: no idle states (cpuidle: $(cat /sys/devices/system/cpu/cpuidle/current_driver 2>/dev/null)), nothing to control"
  exit 0
fi

for s in /sys/devices/system/cpu/cpu$CPU/cpuidle/state*; do
  n=${s##*state}
  if [ "$n" -le "$N" ]; then echo 0 > "$s/disable"; else echo 1 > "$s/disable"; fi
done

on=""
for s in /sys/devices/system/cpu/cpu$CPU/cpuidle/state*; do
  if [ "$(cat "$s/disable")" = 0 ]; then on="$on $(cat "$s/name")"; fi
done
echo "core $CPU, allowed:$on"
