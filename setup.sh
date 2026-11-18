#!/usr/bin/env bash
# Prepare the server for measurement. Not persistent: rerun after reboot.
# Measurement core CPU and system cores OTHERS come from topo.sh (override: CPU=5 OTHERS=0-4 ./setup.sh).

set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

w() { { echo "$2" > "$1"; } 2>/dev/null || true; }   # sysfs write; errors ignored since not every file exists on every CPU/driver

echo "1. Packages: compiler, CMake, OpenSSL, zlib, nlohmann-json, git, curl and matplotlib for plots"
if ! command -v g++ >/dev/null || ! command -v cmake >/dev/null || ! command -v git >/dev/null ||
   ! [ -f /usr/include/nlohmann/json.hpp ] || ! [ -f /usr/include/openssl/ssl.h ] || ! [ -f /usr/include/zlib.h ] ||
   ! python3 -c 'import matplotlib' 2>/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential cmake libssl-dev zlib1g-dev \
    nlohmann-json3-dev git curl python3-matplotlib python3-numpy >/dev/null
fi

echo "1b. Handler and data: CppTrader (~90 MB) and a Nasdaq BX trading day (391 MB, ~1 GB on disk)"
./get-cpptrader.sh
./get-itch.sh
. ./build.sh
build || echo "!! build failed: run cmake --build build to see the error"

echo "2. SMT off: the measurement core gets no sibling thread on its physical core"
w /sys/devices/system/cpu/smt/control off

. ./topo.sh   # only after SMT is off, so the topology has no sibling threads
echo "   measurement core $CPU, system and interrupts on $OTHERS"

echo "3. Turbo off, frequency pinned to base"
w /sys/devices/system/cpu/intel_pstate/no_turbo 1
w /sys/devices/system/cpu/cpufreq/boost 0
# AMD: amd-pstate in active (EPP) mode won't pin the frequency, so switch to passive; disable boost globally and per policy.
[ "$(cat /sys/devices/system/cpu/amd_pstate/status 2>/dev/null)" = active ] && w /sys/devices/system/cpu/amd_pstate/status passive
w /sys/devices/system/cpu/cpufreq/boost 0   # again: switching the amd-pstate mode re-enables global boost
for b in /sys/devices/system/cpu/cpufreq/policy*/boost; do w "$b" 0; done
for d in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do
  # skip cores taken offline with SMT: their cpufreq files are unreadable
  base=$(cat "$d/base_frequency" 2>/dev/null || cat "$d/amd_pstate_nominal_freq" 2>/dev/null ||
         cat "$d/cpuinfo_max_freq" 2>/dev/null) || continue
  w "$d/scaling_governor" performance
  w "$d/scaling_max_freq" "$base"
  w "$d/scaling_min_freq" "$base"
done

echo "4. Interrupts and kernel background work on cores $OTHERS"
systemctl stop irqbalance 2>/dev/null || true
for f in /proc/irq/*/smp_affinity_list; do w "$f" "$OTHERS"; done
# Workqueues only on the system cores (OTHERS): on AMD the rest of the measured CCD must stay empty.
mask=0; IFS=, read -ra parts <<< "$OTHERS"
for part in "${parts[@]}"; do for ((c = ${part%-*}; c <= ${part#*-}; c++)); do mask=$((mask | 1 << c)); done; done
w /sys/devices/virtual/workqueue/cpumask "$(printf '%x' $mask)"

echo "5. All system processes on cores $OTHERS (core $CPU is left for bench only)"
for u in system.slice user.slice init.scope; do
  systemctl set-property --runtime "$u" AllowedCPUs="$OTHERS"
done

echo "6. Misc: no THP, no NMI watchdog, no RT runtime limit, no automatic updates"
w /sys/kernel/mm/transparent_hugepage/enabled never
sysctl -qw kernel.nmi_watchdog=0 kernel.sched_rt_runtime_us=-1 kernel.perf_event_paranoid=-1 vm.stat_interval=120
systemctl stop unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true

echo
./check.sh
