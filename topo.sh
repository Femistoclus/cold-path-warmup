# topo.sh — measurement core CPU and system cores OTHERS from the L3 topology; source with ".".
# CPU: last core of the last L3 domain; OTHERS: cores of the first L3 domain (with one domain, all but CPU).
# i7-8700 (one L3, 6 cores, SMT off): CPU=5, OTHERS=0-4. Ryzen 9950X (two CCDs): CPU=15, OTHERS=0-7,
# cores 8–14 (CCD neighbours) stay idle. Override: CPU=5 OTHERS=0-4 ./setup.sh. Evaluate only after SMT is off.

if [ -z "${CPU:-}" ] || [ -z "${OTHERS:-}" ]; then
  _doms=$( { for _c in /sys/devices/system/cpu/cpu[0-9]*; do
              [ "$(cat "$_c/online" 2>/dev/null || echo 1)" = 1 ] && cat "$_c/cache/index3/shared_cpu_list" 2>/dev/null
            done; true; } | sort -u | sort -t- -k1,1n || true)
  _first=$(echo "$_doms" | head -1); _last=$(echo "$_doms" | tail -1)
  if [ -z "$_first" ]; then _first="0-5"; _last="0-5"; fi
  _hi=${_last##*[-,]}
  CPU=${CPU:-$_hi}
  if [ -z "${OTHERS:-}" ]; then
    if [ "$_first" = "$_last" ]; then OTHERS="${_first%%[-,]*}-$((CPU - 1))"; else OTHERS="$_first"; fi
  fi
  unset _doms _first _last _hi _c
fi
