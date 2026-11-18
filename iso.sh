#!/usr/bin/env bash
# iso.sh — isolation level of the measurement core for one run (stage 1, no CAT). Needs root.
#   ./iso.sh LEVEL [LOAD]           — switch to LEVEL; the neighbour starts and waits for $ISO_DIR/nb.ready
#   ./iso.sh stop                   — stop the neighbour, restore IRQ and workqueue masks (back to ded)
#   ./iso.sh show                   — actual state; the first line "isolation: ..." goes to env.txt
#   ./iso.sh solo LOAD CORE SECONDS — the neighbour alone, no bench (result in $ISO_DIR/nb.out)
# Levels (core CPU from topo.sh, 5 on i7-8700):
#   ded     nothing on core CPU (setup.sh as is);
#   os      all IRQs and unbound workqueues allowed on CPU as well; no processes;
#   os-irq  os + disk IRQs bound to CPU and O_DIRECT 4 KB reads, IO_RATE per second, on core IO_CPU;
#   l3      the neighbour on core NB_CPU (default CPU−1): shares only L3;
#   shared  the neighbour on core CPU itself (SCHED_OTHER; bench is SCHED_FIFO and preempts it).
# Loads: nop, mem:256k, mem:64m, code:128k, mix (g++ -O2 -c of a CppTrader file in a loop, object in /dev/shm).
# Env: ISO_DIR (logs, ready file; default /tmp/coldpath-iso), NB_CPU, IO_CPU (first system core), IO_RATE (3000),
#   IRQS — disk IRQ numbers if auto-detection picks the wrong ones.
# What to restore is kept in /run/coldpath-iso; run.sh and iso-night.sh call "stop" on exit.

set -uo pipefail
cd "$(dirname "$0")"
. ./topo.sh

STATE=/run/coldpath-iso
CNT=/dev/shm/coldpath-nb.cnt
ISO_DIR=${ISO_DIR:-/tmp/coldpath-iso}
NB_CPU=${NB_CPU:-$((CPU - 1))}
IO_CPU=${IO_CPU:-${OTHERS%%[-,]*}}
IO_RATE=${IO_RATE:-3000}
MIX_CMD="g++ -std=c++20 -O2 -w -I$PWD/third_party/CppTrader/include -I$PWD/third_party/CppCommon/include"
MIX_CMD="$MIX_CMD -I$PWD/third_party/fmt/include -c $PWD/third_party/CppTrader/source/trader/matching/market_manager.cpp"
MIX_CMD="$MIX_CMD -o /dev/shm/coldpath-mix.o"

w() { { echo "$2" > "$1"; } 2>/dev/null; }

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
mkdir -p "$STATE" "$ISO_DIR"

in_list() { # CPU LIST: is CPU in a list like 0-4,6
  local c=$1 r parts
  IFS=, read -ra parts <<< "$2"
  for r in "${parts[@]}"; do
    if [[ $r == *-* ]]; then
      [ "$c" -ge "${r%-*}" ] && [ "$c" -le "${r#*-}" ] && return 0
    elif [ "$c" = "$r" ]; then
      return 0
    fi
  done
  return 1
}

irq_save() { # once per level: affinity of every IRQ and the workqueue mask, restored by stop
  [ -f "$STATE/irq.txt" ] && return 0
  local f n
  for f in /proc/irq/[0-9]*/smp_affinity_list; do
    n=${f#/proc/irq/}; echo "${n%%/*} $(cat "$f")"
  done > "$STATE/irq.txt"
  cat /sys/devices/virtual/workqueue/cpumask > "$STATE/wq.txt" 2>/dev/null
}

irq_restore() {
  [ -f "$STATE/irq.txt" ] || return 0
  local n m
  while read -r n m; do w "/proc/irq/$n/smp_affinity_list" "$m"; done < "$STATE/irq.txt"
  [ -s "$STATE/wq.txt" ] && w /sys/devices/virtual/workqueue/cpumask "$(cat "$STATE/wq.txt")"
  rm -f "$STATE/irq.txt" "$STATE/wq.txt"
}

os_open() { # IRQs and unbound workqueues may also go to core CPU
  irq_save
  local f wq
  for f in /proc/irq/[0-9]*/smp_affinity_list; do w "$f" "$OTHERS,$CPU"; done
  wq=$(tr -d ',\n' < "$STATE/wq.txt")
  w /sys/devices/virtual/workqueue/cpumask "$(printf '%x' $((0x${wq:-0} | (1 << CPU))))"
}

disk_irqs() {
  if [ -n "${IRQS:-}" ]; then echo "$IRQS"; return; fi
  awk '/ahci|nvme[0-9]+q[1-9]|virtio[0-9]+-req|megasas|mpt3sas|ata_piix/ { sub(":", "", $1); print $1 }' /proc/interrupts
}

disk_irqs_to_cpu() {
  local n moved=""
  for n in $(disk_irqs); do
    w "/proc/irq/$n/smp_affinity_list" "$CPU"
    in_list "$CPU" "$(cat "/proc/irq/$n/effective_affinity_list" 2>/dev/null)" && moved="$moved $n"
  done
  echo "disk IRQs on core $CPU:${moved:- none}"
  [ -n "$moved" ] || echo "!! no disk IRQ moved (managed NVMe IRQs?) — os-irq here = os + reads; set IRQS=..."
}

# start_bg NAME CORE ARGS... — build/neighbour in scope coldpath-NAME pinned to CORE; output in $ISO_DIR/NAME.out
start_bg() {
  local name=$1 core=$2 pid; shift 2
  systemctl stop "coldpath-$name.scope" 2>/dev/null
  systemctl reset-failed "coldpath-$name.scope" 2>/dev/null
  if [ -n "$(systemctl show -p AllowedCPUs --value user.slice 2>/dev/null)" ]; then
    # systemd-run --scope execs the command, so $! is the neighbour itself.
    systemd-run --quiet --scope --unit="coldpath-$name" --slice=bench -p AllowedCPUs="$core" \
      build/neighbour --cpu "$core" --log "$ISO_DIR/$name.log" "$@" > "$ISO_DIR/$name.out" 2>&1 &
  else
    taskset -c "$core" build/neighbour --cpu "$core" --log "$ISO_DIR/$name.log" "$@" > "$ISO_DIR/$name.out" 2>&1 &
  fi
  pid=$!
  echo "$pid" > "$STATE/$name.pid"
  sleep 0.5
  kill -0 "$pid" 2>/dev/null || { echo "!! neighbour $name failed to start:"; cat "$ISO_DIR/$name.out"; return 1; }
}

stop_bg() { # NAME — saves the neighbour's status (context switches) before SIGTERM
  local name=$1 pid
  pid=$(cat "$STATE/$name.pid" 2>/dev/null) || return 0
  if kill -0 "$pid" 2>/dev/null; then
    { echo "pid $pid"; taskset -cp "$pid"
      grep -E "^(Cpus_allowed_list|voluntary_ctxt_switches|nonvoluntary_ctxt_switches)" "/proc/$pid/status"
    } > "$(cat "$STATE/dir" 2>/dev/null || echo "$ISO_DIR")/$name-status.txt" 2>&1
    kill -TERM "$pid"
    for _ in $(seq 100); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
    kill -KILL "$pid" 2>/dev/null
  fi
  systemctl stop "coldpath-$name.scope" 2>/dev/null   # leftovers such as g++ of mix
  rm -f "$STATE/$name.pid"
}

nb_args() { # LOAD → neighbour options
  case "$1" in
    mix) echo "--load exec" ;;
    code | code:128k) echo "--load code" ;;
    nop | mem:*) echo "--load $1" ;;
    *) echo "!! unknown load '$1': nop, mem:256k, mem:64m, code:128k, mix" >&2; return 1 ;;
  esac
}

start_nb() { # CORE LOAD
  local args
  args=$(nb_args "$2") || return 1
  rm -f "$CNT" "$ISO_DIR/nb.ready"
  # shellcheck disable=SC2086
  if [ "$2" = mix ]; then
    start_bg nb "$1" $args --cmd "$MIX_CMD" --counter "$CNT" --wait-file "$ISO_DIR/nb.ready"
  else
    start_bg nb "$1" $args --counter "$CNT" --wait-file "$ISO_DIR/nb.ready"
  fi || return 1
  for _ in $(seq 50); do [ -f "$CNT" ] && return 0; sleep 0.1; done
  echo "!! no counter file $CNT"; return 1
}

stop_all() {
  stop_bg nb
  stop_bg io
  irq_restore
  rm -f "$CNT" "$STATE/level" "$STATE/dir"
}

show() {
  local level pid f n=0 list=""
  level=$(cat "$STATE/level" 2>/dev/null || echo level=ded)
  for f in /proc/irq/[0-9]*/effective_affinity_list; do
    if in_list "$CPU" "$(cat "$f" 2>/dev/null)"; then
      n=$((n + 1)); f=${f#/proc/irq/}; list="$list ${f%%/*}"
    fi
  done
  echo "isolation: $level cpu=$CPU nb_cpu=$NB_CPU irq_on_cpu=$n wq=$(cat /sys/devices/virtual/workqueue/cpumask 2>/dev/null)"
  echo "IRQs with effective affinity to core $CPU:${list:- none}"
  for f in nb io; do
    pid=$(cat "$STATE/$f.pid" 2>/dev/null) && kill -0 "$pid" 2>/dev/null && echo "$f: $(taskset -cp "$pid")"
  done
  echo "AllowedCPUs: user.slice=$(systemctl show -p AllowedCPUs --value user.slice 2>/dev/null)" \
       "system.slice=$(systemctl show -p AllowedCPUs --value system.slice 2>/dev/null)"
}

level() { # LEVEL [LOAD]
  local lv=$1 load=${2:-}
  stop_all
  [ "$lv" = ded ] || [ "$lv" = os ] || [ -x build/neighbour ] || { echo "no build/neighbour, build it first"; return 1; }
  echo "$ISO_DIR" > "$STATE/dir"
  case "$lv" in
    ded) ;;
    os) os_open ;;
    os-irq)
      os_open
      disk_irqs_to_cpu
      rm -f "$ISO_DIR/nb.ready"
      local file=${ITCH_FILE:-data/20190730.BX_ITCH_50}
      [[ $file == /* ]] || file=$PWD/$file
      start_bg io "$IO_CPU" --load "io:$IO_RATE" --io-file "$file" \
        --wait-file "$ISO_DIR/nb.ready" || { stop_all; return 1; }
      ;;
    l3 | shared)
      [ -n "$load" ] || { echo "level $lv needs a load"; return 1; }
      local core=$CPU
      [ "$lv" = l3 ] && core=$NB_CPU
      start_nb "$core" "$load" || { stop_all; return 1; }
      ;;
    *) echo "unknown level $lv: ded, os, os-irq, l3, shared"; return 1 ;;
  esac
  echo "level=$lv${load:+ load=$load}" > "$STATE/level"
  show
}

solo() { # LOAD CORE SECONDS
  local args pid
  args=$(nb_args "$1") || return 1
  stop_bg nb
  echo "$ISO_DIR" > "$STATE/dir"
  rm -f "$CNT"
  # shellcheck disable=SC2086
  if [ "$1" = mix ]; then
    start_bg nb "$2" $args --cmd "$MIX_CMD" --counter "$CNT" --duration "$3"
  else
    start_bg nb "$2" $args --counter "$CNT" --duration "$3"
  fi || return 1
  pid=$(cat "$STATE/nb.pid")
  while kill -0 "$pid" 2>/dev/null; do sleep 1; done
  rm -f "$STATE/nb.pid" "$CNT" "$STATE/dir"
  grep "^total:" "$ISO_DIR/nb.out"
}

cmd=${1:?"usage: see the header of iso.sh"}; shift
case "$cmd" in
  stop) stop_all ;;
  show) show ;;
  solo) solo "$@" ;;
  *) level "$cmd" "$@" ;;
esac
