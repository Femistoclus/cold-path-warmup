#!/usr/bin/env python3
"""export.py — compact curves of online-bench series for figures (results/online/export.csv).

Usage: python3 export.py results/online/SERIES [...]

Rows: series, arch, mode, metric, x, y, n. Metrics:
  handler_bin — handler time (ns) of watched-pair packets by pause before them (log bins, ms): median, p90, p99;
  rare_ccdf, system_ccdf — latency (µs) quantile at 1 − 10^-x for the watched pair and for the other pairs (group all).
"""
import csv
import os
import sys
from collections import defaultdict

import numpy as np

from online import expand, load_block, pauses
from tails import measure, watched_locate

EDGES = np.logspace(-2, 4, 25)  # ms
XS = np.round(np.arange(0.3, 5.01, 0.1), 2)


def main():
    out_rows = []
    for path in sys.argv[1:]:
        series = os.path.basename(path.rstrip("/"))
        acc = defaultdict(lambda: defaultdict(list))
        for d in expand([path]):
            grp, recs = load_block(d)
            for (cpu, mode), v in recs.items():
                r = v["r"]
                wait, handler, ok = measure(r, v["ghz"])
                loc = watched_locate(r)
                mine = r["locate"] == loc if loc is not None else np.full(len(r), grp == "one")
                gap, _ = pauses(r, grp)
                sel = ok & mine & ~np.isnan(gap)
                a = acc[(grp, mode)]
                a["h"].append(handler[sel])
                a["gap"].append(gap[sel])
                a["rare"].append((wait + handler)[ok & mine] / 1e3)
                if grp == "all":
                    a["sys"].append(((wait + handler)[ok & ~mine] / 1e3).astype(np.float32))
        for (grp, mode), a in acc.items():
            arch = "A" if grp == "all" else "B"
            h, gap = np.concatenate(a["h"]), np.concatenate(a["gap"])
            for lo, hi in zip(EDGES[:-1], EDGES[1:]):
                s = h[(gap >= lo) & (gap < hi)]
                if len(s) >= 20:
                    mid = float(np.sqrt(lo * hi))
                    for name, q in (("median", .5), ("p90", .9), ("p99", .99)):
                        out_rows.append([series, arch, mode, f"handler_bin_{name}", mid, float(np.quantile(s, q)), len(s)])
            for key, metric in (("rare", "rare_ccdf"), ("sys", "system_ccdf")):
                if not a[key]:
                    continue
                x = np.concatenate(a[key])
                for e in XS:
                    q = 1 - 10 ** (-e)
                    if len(x) * (1 - q) >= 10:
                        out_rows.append([series, arch, mode, metric, float(e), float(np.quantile(x, q)), len(x)])
    os.makedirs("results/online", exist_ok=True)
    with open("results/online/export.csv", "a", newline="") as f:
        csv.writer(f).writerows(out_rows)
    print(f"rows: {len(out_rows)}")


if __name__ == "__main__":
    main()
