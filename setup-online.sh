#!/usr/bin/env bash
# setup-online.sh — prepare a server for the online bench (not persistent: rerun after reboot).
# Core 0: system and interrupts; core 1: connector (feed); cores 2–5: receivers (recv).
# SMT, turbo, frequency, THP etc. as in setup.sh; only the core sets and the network differ.

set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

w() { { echo "$2" > "$1"; } 2>/dev/null || true; }

echo "0. Build packages"
if ! command -v cmake >/dev/null || ! [ -f /usr/include/nlohmann/json.hpp ] || ! [ -f /usr/include/openssl/ssl.h ] ||
   ! [ -f /usr/include/zlib.h ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential cmake libssl-dev zlib1g-dev nlohmann-json3-dev >/dev/null
fi

echo "1. SMT off, turbo off, base frequency (as in setup.sh)"
w /sys/devices/system/cpu/smt/control off
w /sys/devices/system/cpu/intel_pstate/no_turbo 1
w /sys/devices/system/cpu/cpufreq/boost 0
for d in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do
  base=$(cat "$d/base_frequency" 2>/dev/null || cat "$d/cpuinfo_max_freq" 2>/dev/null) || continue
  w "$d/scaling_governor" performance
  w "$d/scaling_max_freq" "$base"
  w "$d/scaling_min_freq" "$base"
done

echo "2. Interrupts and background work on core 0; system processes on core 0"
systemctl stop irqbalance 2>/dev/null || true
for f in /proc/irq/*/smp_affinity_list; do w "$f" 0; done
w /sys/devices/virtual/workqueue/cpumask 1
for u in system.slice user.slice init.scope; do systemctl set-property --runtime "$u" AllowedCPUs=0; done

echo "3. Misc: no THP, no NMI watchdog, no RT runtime limit, no automatic updates"
w /sys/kernel/mm/transparent_hugepage/enabled never
sysctl -qw kernel.nmi_watchdog=0 kernel.sched_rt_runtime_us=-1 kernel.perf_event_paranoid=-1 vm.stat_interval=120
systemctl stop unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true

echo "4. Network: multicast on loopback, route 239.192.0.0/16, socket buffers up to 32 MB"
ip link set lo multicast on
ip route replace 239.192.0.0/16 dev lo
sysctl -qw net.core.rmem_max=33554432 net.core.wmem_max=8388608

echo "5. Build"
. ./build.sh
build || echo "!! build failed: run cmake --build build to see the error"
for c in 1 2 3 4 5; do CPU=$c ./cstate.sh 8 >/dev/null; done

echo "done: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | xargs); kernel $(uname -r)"
