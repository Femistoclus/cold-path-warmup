#!/usr/bin/env python3
"""Analyse online-bench receiver journals.

Usage:
  python3 online.py results/online/SERIES [...]              all blocks of a series (or block dirs)
  python3 online.py --watch-loc=N results/online/SERIES      group all: only the watched pair N
  python3 online.py --hash results/online/SERIES             compare book hashes across receivers

Per packet: handler time (TSC cycles → ns at the TSC frequency from the receiver log), the pause
before it, latency = kernel timestamp → end of handler, and wake-up = kernel timestamp → user-space
receive. Pauses use the kernel timestamp, which is the same for all receivers of a block.
Pause: since the previous message of the same pair. In group one (architecture B, quiet pair only)
that is the previous packet; group all is architecture A (full feed).
Hot reference: median handler time of packets that arrived < 20 µs after the previous packet of
any pair, per receiver.
Excluded: packets after a book hash or journal flush (flags 4, 1) and service packets (seq 0).
"""
import glob
import os
import re
import sys
from collections import defaultdict

import numpy as np

REC = np.dtype([("seq", "<u8"), ("k_ns", "<u8"), ("u_ns", "<u8"), ("s_ns", "<u8"), ("cyc", "<u4"),
                ("warm_ns", "<u4"), ("irq_ns", "<u4"), ("locate", "<u2"), ("warms", "<u2"), ("type", "u1"),
                ("nmsg", "u1"), ("sig", "u1"), ("flags", "u1"), ("pad", "<u4")])
BINS = [(0, 0.1), (0.1, 1), (1, 10), (10, 100), (100, 1000), (1000, 1e9)]  # ms
BIN_NAME = ["< 0.1 ms", "0.1–1 ms", "1–10 ms", "10–100 ms", "0.1–1 s", "> 1 s"]
ORDER = ["spinshadow", "spin", "spinpf", "sleepshadow", "sleep"]


def load_block(d):
    lay = open(os.path.join(d, "layout.txt")).read()
    grp = re.search(r"group=(\S+)", lay).group(1)
    out = {}
    for f in sorted(glob.glob(os.path.join(d, "cpu*-*.rec"))):
        m = re.match(r"cpu(\d+)-(\w+)\.rec", os.path.basename(f))
        cpu, mode = int(m.group(1)), m.group(2)
        r = np.fromfile(f, dtype=REC)
        logf = f.replace(".rec", ".log")
        ghz = 3.192
        if os.path.exists(logf):
            mm = re.search(r"TSC ([\d.]+)", open(logf).read())
            if mm:
                ghz = float(mm.group(1))
        r = r[(r["seq"] > 0)]
        if not len(r):
            continue
        out[(cpu, mode)] = {"r": r, "ns": r["cyc"] / ghz, "ghz": ghz}
    return grp, out


def pauses(r, grp):
    """Pause before each packet, ms: since the previous message of the same pair, and since the
    previous packet of any pair."""
    k = r["k_ns"].astype(np.int64)
    gap = np.full(len(r), np.nan)
    last = {}
    for i, (loc, t) in enumerate(zip(r["locate"], k)):
        if loc in last:
            gap[i] = (t - last[loc]) / 1e6
        last[loc] = t
    anyg = np.r_[np.nan, np.diff(k) / 1e6]
    return gap, anyg


def binned(x, gap):
    rows = []
    for lo, hi in BINS:
        s = x[(gap >= lo) & (gap < hi)]
        rows.append((len(s), np.median(s) if len(s) else np.nan))
    return rows


def analyze(dirs, watch_loc=None):
    agg = defaultdict(lambda: defaultdict(list))  # (group, mode) → metric → per-receiver arrays
    for d in dirs:
        grp, recs = load_block(d)
        for (cpu, mode), v in recs.items():
            r, ns = v["r"], v["ns"]
            ok = (r["flags"] & 5) == 0
            gap, anyg = pauses(r, grp)
            lat = (r["u_ns"].astype(np.int64) - r["k_ns"].astype(np.int64)) / 1e3 + ns / 1e3  # µs
            wake = (r["u_ns"].astype(np.int64) - r["k_ns"].astype(np.int64)) / 1e3
            sel = ok & ~np.isnan(gap)
            if watch_loc is not None and grp == "all":
                sel &= r["locate"] == watch_loc
            a = agg[(grp, mode)]
            a["ns"].append(ns[sel])
            a["gap"].append(gap[sel])
            a["lat"].append(lat[sel])
            a["wake"].append(wake[sel])
            a["type"].append(r["type"][sel])
            hot = ok & (anyg < 0.02)
            a["hot"].append(ns[hot])
            a["hot_type"].append(r["type"][hot])
    return agg


def report(agg, title):
    print(f"\n=== {title}")
    for grp in ("one", "all"):
        known = [m for m in ORDER if (grp, m) in agg]
        modes = known + sorted(m for g, m in agg if g == grp and m not in known)
        if not modes:
            continue
        arch = "B: quiet pair only" if grp == "one" else "A: full feed"
        print(f"\nArchitecture {arch}. Handler time, median, ns (message count in brackets)")
        print(f"  {'pause before message':<24}" + "".join(f"{m:>18}" for m in modes))
        cols = {}
        for m in modes:
            a = agg[(grp, m)]
            ns, gap = np.concatenate(a["ns"]), np.concatenate(a["gap"])
            cols[m] = binned(ns, gap)
        for i, name in enumerate(BIN_NAME):
            cells = [f"{cols[m][i][1]:.0f} ({cols[m][i][0]})" if cols[m][i][0] else "—" for m in modes]
            print(f"  {name:<24}" + "".join(f"{c:>18}" for c in cells))
        hot = []
        for m in modes:
            h = np.concatenate(agg[(grp, m)]["hot"])
            hot.append(f"{np.median(h):.0f} ({len(h)})" if len(h) else "—")
        print(f"  {'reference: pause < 20 us':<24}" + "".join(f"{c:>18}" for c in hot))
        print('\n  Latency "kernel → end of handler", us, pauses ≥ 10 ms: p50 / p90 / p99; wake-up p50')
        for m in modes:
            a = agg[(grp, m)]
            lat, gap, wake = np.concatenate(a["lat"]), np.concatenate(a["gap"]), np.concatenate(a["wake"])
            s, w = lat[gap >= 10], wake[gap >= 10]
            if len(s):
                print(f"  {m:<14} {np.percentile(s, 50):8.1f} {np.percentile(s, 90):8.1f} {np.percentile(s, 99):8.1f}"
                      f"   {np.median(w):8.1f}   (n={len(s)})")


def hashes(dirs):
    for d in dirs:
        hs = {}
        for f in sorted(glob.glob(os.path.join(d, "cpu*.rec.hash"))):
            hs[os.path.basename(f)] = dict(l.split()[-2:] for l in open(f) if l.strip())
        if not hs:
            continue
        names = list(hs)
        common = set.intersection(*(set(h) for h in hs.values()))
        bad = [s for s in common if len({hs[n][s] for n in names}) > 1]
        print(f"{d}: receivers {len(names)}, common points {len(common)}, mismatches {len(bad)}")


def expand(paths):
    out = []
    for p in paths:
        if os.path.exists(os.path.join(p, "layout.txt")):
            out.append(p)
        else:
            out += sorted(os.path.dirname(x) for x in glob.glob(os.path.join(p, "*", "layout.txt")))
    return out


def main():
    args = sys.argv[1:]
    if args and args[0] == "--hash":
        hashes(expand(args[1:]))
        return
    watch = None
    if args and args[0].startswith("--watch-loc="):
        watch = int(args.pop(0).split("=")[1])
    dirs = expand(args)
    if not dirs:
        sys.exit("no blocks")
    report(analyze(dirs, watch), f"{len(dirs)} blocks: {', '.join(sorted({os.path.basename(os.path.dirname(d)) for d in dirs}))}")


if __name__ == "__main__":
    main()
