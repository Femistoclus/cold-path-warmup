#!/usr/bin/env bash
# Environment snapshot, saved as env.txt next to each run's results.

. "$(dirname "$0")/topo.sh"

f() { cat "$1" 2>/dev/null; }

cf=/sys/devices/system/cpu/cpu$CPU/cpufreq

echo "date: $(date -u +%FT%TZ)"
echo "host: $(hostname)"
echo "machine: $(f /sys/class/dmi/id/sys_vendor) $(f /sys/class/dmi/id/product_name) / board $(f /sys/class/dmi/id/board_name)"
echo "cpu: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | xargs)"
echo "microcode: $(grep -m1 microcode /proc/cpuinfo | awk '{print $3}')"
echo "kernel: $(uname -r)"
echo "cmdline: $(f /proc/cmdline)"
echo "smt_active: $(f /sys/devices/system/cpu/smt/active)"
echo "no_turbo: $(f /sys/devices/system/cpu/intel_pstate/no_turbo); boost: $(f /sys/devices/system/cpu/cpufreq/boost) $(f $cf/boost)"
echo "cpufreq driver: $(f $cf/scaling_driver); amd_pstate: $(f /sys/devices/system/cpu/amd_pstate/status)"
echo "cpu id: $(grep -m1 "vendor_id" /proc/cpuinfo | cut -d: -f2 | xargs) family $(grep -m1 "cpu family" /proc/cpuinfo | cut -d: -f2 | xargs) model $(grep -m1 "^model\s" /proc/cpuinfo | cut -d: -f2 | xargs) stepping $(grep -m1 stepping /proc/cpuinfo | cut -d: -f2 | xargs)"
echo "bios: $(f /sys/class/dmi/id/bios_vendor) $(f /sys/class/dmi/id/bios_version) $(f /sys/class/dmi/id/bios_date)"
echo "cpu$CPU L3 shared: $(f /sys/devices/system/cpu/cpu$CPU/cache/index3/shared_cpu_list); system cpus: ${OTHERS:-}"
echo "cpu flags: $(grep -m1 "^flags" /proc/cpuinfo | grep -o -w "prefetchi\|lfence_rdtsc\|constant_tsc\|nonstop_tsc\|pcid\|invpcid" | xargs)"
echo "cpu$CPU: governor=$(f $cf/scaling_governor) min=$(f $cf/scaling_min_freq) max=$(f $cf/scaling_max_freq) kHz"
echo "resctrl: cat_l3=$(grep -q -w cat_l3 /proc/cpuinfo && echo yes || echo no) mounted=$(grep -q resctrl /proc/mounts && echo yes || echo no)"
echo "allowed_cpus user.slice: $(systemctl show -p AllowedCPUs --value user.slice 2>/dev/null)"
echo "thp: $(f /sys/kernel/mm/transparent_hugepage/enabled)"
echo "vm.stat_interval: $(sysctl -n vm.stat_interval 2>/dev/null)"
echo "cpuidle: $(f /sys/devices/system/cpu/cpuidle/current_driver) / $(f /sys/devices/system/cpu/cpuidle/current_governor)"
echo "compiler: $(${CC:-gcc} --version 2>/dev/null | head -1)"

echo "c-states cpu$CPU:"
for s in /sys/devices/system/cpu/cpu$CPU/cpuidle/state*; do
  echo "  $(f $s/name) ($(f $s/desc)): latency=$(f $s/latency) us, residency=$(f $s/residency) us, disabled=$(f $s/disable)"
done

echo "vulnerabilities:"
for v in /sys/devices/system/cpu/vulnerabilities/*; do echo "  $(basename $v): $(f $v)"; done

echo "handler: ${HANDLER:-cpptrader}; ITCH_FILE=${ITCH_FILE:-data/20190730.BX_ITCH_50}; ITCH_UNTIL=${ITCH_UNTIL:-10:00:00}"
for d in third_party/*/; do echo "  $(basename "$d"): $(git -C "$d" rev-parse --short HEAD 2>/dev/null)"; done
echo "  data: $(ls -l "${ITCH_FILE:-data/20190730.BX_ITCH_50}" 2>/dev/null | awk '{print $5, $9}')"
echo "git: $(git rev-parse --short HEAD 2>/dev/null || echo none)$(git status --porcelain 2>/dev/null | grep -q . && echo ' (uncommitted changes)')"
