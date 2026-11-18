#!/usr/bin/env bash
# dis.sh — targeted disruption series, Intel i7-8700; run as root like pf.sh.
#   ./dis.sh                 # step 0 (huge pages, msr, event codes), then series A B C D E F G
#   ./dis.sh A B             # selected series only
#   SMOKE=1 ./dis.sh A B     # step 1 of the plan: 300 trials, one run
# A singles, time; B singles, counters a–e; C dose curves; D pairs and synthetic C3/C6;
# E real C-states + disruption after sleep (--after-pause); F natural decay with counters;
# G method controls (settle 0/1/10 µs, A/A, order of a pair, repeat of A for drift).
# Labels of the synthetic cells are repeated in disrupt.py.

set -uo pipefail
cd "$(dirname "$0")"

pad() { head -c $(($1 * 700)) /dev/zero | tr '\0' x; }

if [ "${SMOKE:-0}" = 1 ]; then
  RUNS="1"; RUNS2="1"; T=300; TB=300; TC=300; TE=300
elif [ "${QUICK:-0}" = 1 ]; then   # cut to ~1 h: 3 runs, 5 thousand trials, C-states in E only C6
  RUNS="1 2 3"; RUNS2="1"; T=5000; TB=1000; TC=2000; TE=1000
else
  RUNS="1 2 3 4 5"; RUNS2="1 2"; T=10000; TB=1000; TC=3000; TE=1000
fi

SINGLES="base,sys,wr0,wr0:10b,mprot1,d1:64k,d1:24k,l1dflush,d2:1m,clf:d,clf:c,dtlb:128,dtlb:3072,itlb:256,itlb:3072"
SINGLES="$SINGLES,tlbflush,line:16k,line:64k,line:128k,line:1m,jumpy:64k,jumpy:128k,jumpy:256k,dirn:16k,dirw:64k,ibpb"

# Dose curves, each in its own run next to base.
C_DATA="d1:4k,d1:8k,d1:16k,d1:24k,d1:32k,d1:48k,d1:64k,d1:128k,d1:256k,d1:512k,d1:1m,d1:2m,d1:4m,d1:16m,d1:64m"
C_LINE="line:4k,line:8k,line:16k,line:32k,line:64k,line:128k,line:256k,line:512k,line:1m,line:2m,line:4m"
C_JUMPY="jumpy:4k,jumpy:8k,jumpy:16k,jumpy:32k,jumpy:64k,jumpy:128k,jumpy:256k,jumpy:512k"
C_DTLB="dtlb:16,dtlb:32,dtlb:64,dtlb:128,dtlb:256,dtlb:512,dtlb:1024,dtlb:2048,dtlb:4096,dtlb:8192"
C_ITLB="itlb:16,itlb:32,itlb:64,itlb:128,itlb:256,itlb:512,itlb:1024,itlb:2048,itlb:4096,itlb:8192"
C_DIRN="dirn:1k,dirn:2k,dirn:4k,dirn:8k,dirn:16k,dirn:32k,dirn:64k,dirn:128k,dirn:256k,dirn:512k,dirn:1m"
C_CLFD="clf:d:1,clf:d:2,clf:d:4,clf:d:8,clf:d:16,clf:d:32,clf:d:46"
C_CLFC="clf:c:1,clf:c:4,clf:c:8,clf:c:16,clf:c:32,clf:c:64,clf:c:85"

# Pairs and synthetic states; order inside a cell: syscalls → code → data → TLB.
D_SINGLES="base,line:64k,line:16k,line:1m,jumpy:128k,jumpy:256k,dirw:64k,dirn:16k,ibpb,d1:64k,d2:1m,dtlb:128,itlb:3072"
D_SINGLES="$D_SINGLES,tlbflush"
D_PAIRS="line:64k+d2:1m,jumpy:128k+d2:1m,d2:1m+tlbflush,line:16k+itlb:3072,jumpy:128k+dirw:64k,ibpb+jumpy:128k"
D_PAIRS="$D_PAIRS,ibpb+dirn:16k,d1:64k+dtlb:128"
SYN_C3="line:1m+d2:1m+tlbflush"
SYN_C6="ibpb+jumpy:256k+dirw:64k+line:1m+d2:1m+tlbflush"
SYN_ALL="ibpb+jumpy:256k+dirw:64k+dirn:16k+line:1m+d2:1m+dtlb:3072+itlb:3072+tlbflush"

step0() {
  echo 48 > /proc/sys/vm/nr_hugepages
  grep -E "HugePages_(Total|Free)" /proc/meminfo
  [ "$(awk '/HugePages_Free/ {print $2}' /proc/meminfo)" -ge 36 ] || echo "!! too few huge pages: runs will use hp=0"
  modprobe msr || echo "!! modprobe msr failed: cells ibpb, l1dflush, wr0 will be skipped"
  echo "CPU flags: $(grep -o -w -E 'ibpb|flush_l1d|pcid|invpcid' /proc/cpuinfo | sort -u | tr '\n' ' ')"
  echo "tlb_single_page_flush_ceiling: $(cat /sys/kernel/debug/x86/tlb_single_page_flush_ceiling 2>/dev/null || echo '?')"
  echo "nohz_full: $(cat /sys/devices/system/cpu/nohz_full 2>/dev/null)"
  # Group e codes (counters.cpp): 0x01c5, 0x00c5, 0x2008, 0x2085, 0x010d.
  perf list --details 2>/dev/null | grep -A2 -i -E "^ +(br_misp_retired\.(conditional|all_branches)|dtlb_load_misses\.stlb_hit|itlb_misses\.stlb_hit|int_misc\.recovery_cycles) " \
    || echo "!! perf list unavailable: check group e event codes by hand"
  ./check.sh || true
}

A() { # $1 — run name prefix (dA; dA2 for the repeat at the end of the night)
  local p=${1:-dA}
  ./cstate.sh 0
  for r in $RUNS; do
    PAD=$(pad $r) ./run.sh "$p-r$r" --conds hot,dis --disrupt "$SINGLES" --seed $r --trials $T --no-counters
    PAD=$(pad $r) HANDLER=null ./run.sh "${p}null-r$r" --conds hot,dis --disrupt "$SINGLES" --seed $r --trials $T --no-counters
  done
  ./run.sh "$p-d3" --conds hot,dis --disrupt base,d3:64m --trials 1000 --no-counters
}

B() {
  ./cstate.sh 0
  for g in a b c d e; do
    ./run.sh "dB-$g" --conds hot,dis --disrupt "$SINGLES,d3:64m" --trials $TB --group $g
    HANDLER=null ./run.sh "dBnull-$g" --conds hot,dis --disrupt "$SINGLES" --trials $TB --group $g
  done
}

C() {
  ./cstate.sh 0
  for c in DATA LINE JUMPY DTLB ITLB DIRN CLFD CLFC; do
    v="C_$c"
    for r in $RUNS2; do
      PAD=$(pad $r) ./run.sh "dC-$c-r$r" --conds hot,dis --disrupt "base,${!v}" --seed $r --trials $TC --no-counters
    done
    for g in b c d e; do
      ./run.sh "dC-$c-$g" --conds hot,dis --disrupt "base,${!v}" --trials 300 --group $g
    done
  done
}

D() {
  ./cstate.sh 0
  local cells="$D_SINGLES,$D_PAIRS,$SYN_C3,$SYN_C6,$SYN_ALL"
  for r in $RUNS; do
    PAD=$(pad $r) ./run.sh "dD-r$r" --conds hot,dis --disrupt "$cells" --seed $r --trials $T --no-counters
  done
  for g in a b c d e; do ./run.sh "dD-$g" --conds hot,dis --disrupt "$cells" --trials $TB --group $g; done
}

E() { # disruption right after a real sleep; plain none / prewake / pfwake stay as references
  local m="--conds hot,none,prewake,pfwake --after-pause none,prewake --dt-ms 10 --warm-lead-us 20 --period-us 50"
  local cells="base,ibpb,jumpy:256k,dirw:64k,ibpb+jumpy:256k+dirw:64k,d2:1m"
  local pols="3 8"; [ "${QUICK:-0}" = 1 ] && pols=8
  for pol in $pols; do
    ./cstate.sh $pol
    for r in $RUNS; do
      PAD=$(pad $r) ./run.sh "dE$pol-r$r" $m --disrupt "$cells" --seed $r --trials $TE --no-counters
    done
    for g in a b c d e; do ./run.sh "dE$pol-$g" $m --disrupt "$cells" --trials 300 --group $g; done
  done
  [ "${QUICK:-0}" = 1 ] && return
  for pol in 1 2; do
    ./cstate.sh $pol
    ./run.sh "dE$pol-r1" --conds hot,none --dt-ms 10 --trials $TE --no-counters
  done
  ./cstate.sh 8
}

F() { # no disruption: counters of the natural decay for the linear model
  for pol in 0 3 8; do
    ./cstate.sh $pol
    for g in a b c d e; do ./run.sh "dF$pol-$g" --conds hot,spin,none --dt-ms 1,10,100 --trials 300 --group $g; done
    for r in 1 2 3; do
      PAD=$(pad $r) ./run.sh "dF$pol-r$r" --conds hot,spin,none --dt-ms 1,10,100 --seed $r --trials $TE --no-counters
    done
  done
  ./cstate.sh 8
}

G() {
  ./cstate.sh 0
  for s in 0 1000 10000; do
    for r in $RUNS2; do
      ./run.sh "dG-settle$s-r$r" --conds hot,dis --disrupt "base,base+base,d2:1m,line:64k,jumpy:128k" --settle-ns $s \
        --seed $r --trials $TC --no-counters
    done
  done
  local perm="base,line:64k,d2:1m,tlbflush,line:64k+d2:1m,d2:1m+line:64k,d2:1m+tlbflush,tlbflush+d2:1m"
  for r in $RUNS2; do
    ./run.sh "dG-perm-r$r" --conds hot,dis --disrupt "$perm" --seed $r --trials $TC --no-counters
  done
  A dA2
  ./cstate.sh 8
}

[ $# -eq 0 ] && set -- 0 A B C D E F G
for s in "$@"; do
  case $s in
    0) step0 ;;
    A | B | C | D | E | F | G) $s ;;
    *) echo "unknown series $s"; exit 1 ;;
  esac
done

python3 disrupt.py results | tee results/dis-summary.txt
