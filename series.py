#!/usr/bin/env python3
"""Summary of the series.sh series: idle policy × wait mode.

For each idle policy, takes all runs without counters (results/*-s<N>-r*), computes the first-call
p50 per mode in each run, and shows the median over runs and the range (min–max): the unit of
replication is a process run, not a trial (STABILIZER 2013).
Penalty = p50(mode) − p50(hot) of the same run.
Window correction: the same penalty for an empty handler (results/*-null) is the cold start of the
measurement code itself; it is subtracted (Paoloni 2010).
Counters come from a separate run, results/*-s<N>-cnt.

Usage: python3 series.py [results dir]
"""
import csv
import glob
import os
import statistics
import sys
from collections import defaultdict

from analyze import DISCARD, NAME, quantile

POLICIES = [("0", "POLL only"), ("1", "up to C1"), ("3", "up to C3"), ("8", "default (up to C10)")]
MODES = ["spin", "spinshadow", "none", "empty", "shadow", "prewake"]


def cells(path):
    """(mode, Δt) → first-call lists: time, ns; lateness, µs; counters."""
    out = defaultdict(lambda: defaultdict(list))
    with open(path) as f:
        for r in csv.DictReader(f):
            if int(r["trial"]) < DISCARD or r["call"] != "1":
                continue
            c = out[(r["cond"], int(r["dt_ms"]))]
            c["ns"].append(float(r["ns"]))
            c["late"].append(float(r["late_us"]))
            for k in ("l1d_miss", "llc_miss", "br_miss"):
                if k in r:
                    c[k].append(float(r[k]))
    return out


def p50(xs):
    return quantile(xs, .5)


def window(root, pol):
    """Penalty of the measurement code itself under this idle policy: (mode, Δt) → ns.
    Empty-handler run results/*-null-s<N> (results/*-null for the default policy); {} if missing."""
    pat = "*-null" if pol == "8" else f"*-null-s{pol}"
    null = sorted(glob.glob(os.path.join(root, pat, "calls.csv")))
    if not null:
        return {}
    z = cells(null[-1])
    if not z.get(("hot", 0), {}).get("ns"):
        return {}  # run still in progress
    h = p50(z[("hot", 0)]["ns"])
    win = {k: p50(v["ns"]) - h for k, v in z.items()}
    print(f"Measurement window (empty handler, {os.path.basename(os.path.dirname(null[-1]))}): hot {h:.0f} ns; "
          "its penalty at Δt = 100 ms: " + ", ".join(f"{k[0]} {v:.0f}" for k, v in sorted(win.items()) if k[1] in (0, 100))
          + " ns")
    return win


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "results"
    for pol, title in POLICIES:
        runs = sorted(glob.glob(os.path.join(root, f"*-s{pol}-r*", "calls.csv")))
        if not runs:
            continue
        win = window(root, pol)
        data = [cells(r) for r in runs]
        hot = [p50(d[("hot", 0)]["ns"]) for d in data]
        print(f"=== Idle policy: {title}; runs: {len(runs)}; hot p50 {statistics.median(hot):.0f} ns "
              f"(range {min(hot):.0f}–{max(hot):.0f})")
        for c in ("cold", "sys"):
            v = [p50(d[(c, 0)]["ns"]) - p50(d[("hot", 0)]["ns"]) for d in data if (c, 0) in d]
            if v:
                print(f"  {NAME[c]:<26} penalty {statistics.median(v):6.0f} ns (range {min(v):.0f}–{max(v):.0f})")
        dts = sorted({k[1] for d in data for k in d if k[0] in MODES})
        print(f"  {'mode':<26} {'Δt':>4} | {'1st call, ns':>13} | {'penalty, ns (range)':>22} | {'−window':>7} | "
              f"{'lateness, us':>14} | event → end, us")
        for c in MODES:
            for dt in dts:
                if not all((c, dt) in d for d in data):
                    continue
                first = [p50(d[(c, dt)]["ns"]) for d in data]
                pen = [p50(d[(c, dt)]["ns"]) - p50(d[("hot", 0)]["ns"]) for d in data]
                late = [p50(d[(c, dt)]["late"]) for d in data]
                e2e = [p50([l + n / 1000 for l, n in zip(d[(c, dt)]["late"], d[(c, dt)]["ns"])]) for d in data]
                corr = statistics.median(pen) - win[(c, dt)] if (c, dt) in win else float("nan")
                print(f"  {NAME[c]:<26} {dt:>4} | {statistics.median(first):13.0f} | "
                      f"{statistics.median(pen):6.0f} ({min(pen):5.0f}–{max(pen):5.0f}) | {corr:7.0f} | "
                      f"{statistics.median(late):14.1f} | {statistics.median(e2e):.1f}")
        cnt = glob.glob(os.path.join(root, f"*-s{pol}-cnt", "calls.csv"))
        if cnt:
            d = cells(sorted(cnt)[-1])
            dt = max(dts)
            print(f"  1st-call counters (separate run, Δt = {dt} ms), mean l1d / llc / br:")
            for c, k in [("hot", 0), ("cold", 0), ("sys", 0)] + [(m, dt) for m in MODES]:
                if (c, k) in d and d[(c, k)]["l1d_miss"]:
                    v = d[(c, k)]
                    print(f"    {NAME[c]:<26} {statistics.mean(v['l1d_miss']):5.0f} {statistics.mean(v['llc_miss']):5.0f} "
                          f"{statistics.mean(v['br_miss']):5.0f}")
        print()


if __name__ == "__main__":
    main()
