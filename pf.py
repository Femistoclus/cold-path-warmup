#!/usr/bin/env python3
"""Summary of the pf.sh series: software prefetch of code and data.

A. Timing (runs *-pA<idle policy>-<set>-r<N>, no counters): first-call penalty over hot of the same
   run, median over runs and range; event → end of first call; cost of one prefetch pass.
B. Counters (runs *-pB<idle policy>-<set>-<counter group>): first-call mean.
C. Sweeps (*-pC*): penalty per mode, single run.

Usage: python3 pf.py [results dir]
"""
import csv
import glob
import os
import re
import statistics
import sys
from collections import defaultdict

from analyze import DISCARD, NAME, quantile

POL = {"3": "up to C3", "8": "default (C6+)", "0": "POLL only"}
SETS = ["0", "d", "c", "dc"]
SET_NAME = {"0": "empty", "d": "data", "c": "code", "dc": "code+data", "g": "known in advance"}
FIXED = ["none", "spin", "spinshadow", "prewake"]  # independent of the prefetch set
PFM = ["pfwake", "pfevent", "spinpf"]
SKIP = {"trial", "cond", "dt_ms", "call", "ns", "late_us", "wakes", "warm_us", "pre_ns", "gap_us"}
SHOW = ["l1d_miss", "l2_miss", "code_l2_miss", "ic_miss", "ic_stall", "br_miss", "baclears", "dsb_uops",
        "dtlb_walk", "itlb_walk"]


def load(path):
    """(mode, Δt) → first-call lists {ns, late, warm, wakes, counters...}."""
    out = defaultdict(lambda: defaultdict(list))
    with open(path) as f:
        for r in csv.DictReader(f):
            if int(r["trial"]) < DISCARD or r["call"] != "1":
                continue
            c = out[(r["cond"], int(r["dt_ms"]))]
            c["ns"].append(float(r["ns"]))
            c["late"].append(float(r["late_us"]))
            c["warm"].append(float(r["warm_us"]))
            c["wakes"].append(int(r["wakes"]))
            for k, v in r.items():
                if k not in SKIP:
                    c[k].append(float(v))
    return out


def p50(xs):
    return quantile(xs, .5)


def runs(root, pat):
    data = []
    for p in sorted(glob.glob(os.path.join(root, pat, "calls.csv"))):
        d = load(p)
        if d.get(("hot", 0), {}).get("ns"):
            data.append(d)
    return data


def pen(d, m, dt):
    return p50(d[(m, dt)]["ns"]) - p50(d[("hot", 0)]["ns"])


def e2e(d, m, dt):
    return p50([l + n / 1000 for l, n in zip(d[(m, dt)]["late"], d[(m, dt)]["ns"])])


def agg(v):
    if not v:
        return "—"
    m = statistics.median(v)
    return f"{m:.0f}" if len(v) == 1 else f"{m:.0f} ({min(v):.0f}–{max(v):.0f})"


def cost(data, m, dt):
    """Cost of one prefetch pass, ns: warm_us / wakes."""
    w = [x / k * 1000 for d in data for x, k in zip(d[(m, dt)]["warm"], d[(m, dt)]["wakes"]) if k > 0]
    return statistics.median(w) if w else float("nan")


def part_a(root, dt=10):
    print(f"A. 1st-call penalty over hot, ns, Δt = {dt} ms: median over 5 runs (range)\n")
    for pol in ("3", "8"):
        by_set = {s: runs(root, f"*-pA{pol}-{s}-r*") for s in SETS}
        allr = [d for s in SETS for d in by_set[s]]
        if not allr:
            continue
        hot = [p50(d[("hot", 0)]["ns"]) for d in allr]
        print(f"=== idle policy: {POL[pol]}; runs {len(allr)}; hot {agg(hot)} ns")
        print(f"  {'mode':<28} {'penalty, ns':>16} {'event → end, us':>21}")
        for m in FIXED:
            v = [pen(d, m, dt) for d in allr if (m, dt) in d]
            e = [e2e(d, m, dt) for d in allr if (m, dt) in d]
            print(f"  {NAME[m]:<28} {agg(v):>16} {statistics.median(e) if e else float('nan'):>21.2f}")
        print(f"  {'prefetch set →':<28}" + "".join(f"{SET_NAME[s]:>18}" for s in SETS) + "   pass cost (dc), ns")
        for m in PFM:
            cells = []
            for s in SETS:
                v = [pen(d, m, dt) for d in by_set[s] if (m, dt) in d]
                cells.append(agg(v))
            c = cost(by_set["dc"], m, dt) if m != "pfevent" else float("nan")
            print(f"  {NAME[m]:<28}" + "".join(f"{x:>18}" for x in cells) + (f"   {c:.0f}" if c == c else ""))
        e = [e2e(d, "pfwake", dt) for d in by_set["dc"]]
        if e:
            print(f"  event → end with prefetch before event (dc): {statistics.median(e):.2f} us")
        print()


def part_b(root, dt=10):
    print("B. 1st-call counters, mean (rdpmc; times in these runs are not for comparison)\n")
    for pol in ("3", "8"):
        rows = defaultdict(dict)  # (set, mode) → {counter: mean}
        for s in SETS:
            for g in "abcd":
                for d in runs(root, f"*-pB{pol}-{s}-{g}"):
                    for (m, k), c in d.items():
                        if k != dt and m != "hot":
                            continue
                        for name, v in c.items():
                            if name in SHOW and v:
                                rows[(s, m)][name] = statistics.mean(v)
        if not rows:
            continue
        cols = [c for c in SHOW if any(c in r for r in rows.values())]
        print(f"=== idle policy: {POL[pol]}")
        print(f"  {'set':<10} {'mode':<26}" + "".join(f"{c:>12}" for c in cols))
        for m in ["hot"] + FIXED + PFM:
            for s in SETS:
                if (s, m) not in rows or (m not in PFM and s != "0"):
                    continue
                r = rows[(s, m)]
                label = SET_NAME[s] if m in PFM else ""
                print(f"  {label:<10} {NAME[m]:<26}" + "".join(f"{r.get(c, float('nan')):12.1f}" for c in cols))
        print()


def part_c(root):
    print("C. Sweeps: 1st-call penalty over hot, ns (one run, 300 trials)\n")
    paths = sorted(glob.glob(os.path.join(root, "*-pC*", "calls.csv")))
    names = sorted({re.sub(r"^\d+-\d+-", "", os.path.basename(os.path.dirname(p))) for p in paths})
    for name in names:
        data = runs(root, f"*-{name}")
        if not data:
            continue
        d = data[-1]
        dts = sorted({k[1] for k in d if k[0] != "hot"})
        for dt in dts:
            parts = [f"{m} {pen(d, m, dt):.0f}" for m in FIXED + PFM if (m, dt) in d]
            print(f"  {name:<16} Δt {dt:>3}: " + ", ".join(parts))
    print()


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "results"
    part_a(root)
    part_b(root)
    part_c(root)


if __name__ == "__main__":
    main()
