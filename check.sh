#!/usr/bin/env bash
# Check that the server is ready for measurement; prints ok or FAIL per item.

. "$(dirname "$0")/topo.sh"
fail=0

chk() { if eval "$2"; then echo "  ok    $1"; else echo "  FAIL  $1"; fail=1; fi; }
f() { cat "$1" 2>/dev/null; }

echo "CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | xargs), microcode $(grep -m1 microcode /proc/cpuinfo | awk '{print $3}')"
echo "Kernel: $(uname -r)"
cf=/sys/devices/system/cpu/cpu$CPU/cpufreq
fmin=$(f $cf/scaling_min_freq); fmax=$(f $cf/scaling_max_freq)

chk "SMT off" '[ "$(f /sys/devices/system/cpu/smt/active)" = 0 ]'
chk "turbo off" '[ "$(f /sys/devices/system/cpu/intel_pstate/no_turbo)" = 1 ] || [ "$(f /sys/devices/system/cpu/cpufreq/boost)" = 0 ] ||
  [ "$(f /sys/devices/system/cpu/cpufreq/policy$CPU/boost)" = 0 ]'
chk "core $CPU frequency pinned: $(( ${fmin:-0} / 1000 ))–$(( ${fmax:-0} / 1000 )) MHz" \
    '[ -n "$fmin" ] && [ "$fmin" = "$fmax" ]'
chk "system processes only on cores $OTHERS" '[ "$(systemctl show -p AllowedCPUs --value user.slice)" = "$OTHERS" ]'

# Non-timer (LOC) interrupts on the measurement core; field 1 is the IRQ label, so CPU N is field N+2.
irqs() { awk -v c=$((CPU + 2)) 'NR > 1 && $1 != "LOC:" { s += $c } END { print s + 0 }' /proc/interrupts; }
a=$(irqs); sleep 2; b=$(irqs)
case "${ISO:-ded}" in
  # iso.sh opens core CPU to the OS on these levels on purpose.
  os | os-irq | shared) echo "  info  core $CPU got $((b - a)) non-timer interrupts in 2 s; ISO=$ISO, not an error" ;;
  *) chk "core $CPU got $((b - a)) non-timer interrupts in 2 s, need < 20" '[ $((b - a)) -lt 20 ]' ;;
esac

chk "THP off" 'grep -q "\[never\]" /sys/kernel/mm/transparent_hugepage/enabled'
chk "NMI watchdog off" '[ "$(f /proc/sys/kernel/nmi_watchdog)" = 0 ]'
chk "CPU counters available (perf_event_paranoid ≤ 1)" '[ "$(f /proc/sys/kernel/perf_event_paranoid)" -le 1 ]'

echo
[ $fail = 0 ] && echo "Ready to measure." || echo "Some checks FAILED."
exit $fail
