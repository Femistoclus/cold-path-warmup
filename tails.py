#!/usr/bin/env python3
"""Online-bench tails: p50, p99, p999 of the rare handler and of the rest of the system.

Usage: python3 tails.py results/online/SERIES [...]

Rare handler: packets of the watched pair. System: packets of the other pairs on the same receiver
(group all, architecture A); warm-up and the cold handler must not raise their tail.
Latency: kernel timestamp → end of handler, so queueing behind warm-up and other packets is included.
Excluded: packets after a book hash or journal flush (flags 4, 1) and packets queued behind them.
All receivers of a block get the same packets with the same kernel timestamp, so modes are compared
within a block: by quantiles and paired by packet number.
"""
import sys
from collections import defaultdict

import numpy as np

from online import ORDER, expand, load_block

QS = (0.5, 0.99, 0.999)
REF = "spin"
WARMLESS = ("spin", "sleep")
MIN_TAIL = 10  # fewer points beyond a quantile: the value is descriptive only
NEAR_NS = 2000  # warm-up ended closer than this before the end of handling: the packet may have waited for it
FAR_NS = 5000  # farther than this: the warm-up certainly did not delay the packet


def watched_locate(r):
    """Locate of the watched pair: strategy signals occur only on it."""
    loc = r["locate"][r["sig"] == 1]
    return int(np.bincount(loc).argmax()) if len(loc) else None


def measure(r, ghz):
    k = r["k_ns"].astype(np.int64)
    u = r["u_ns"].astype(np.int64)
    handler = r["cyc"] / ghz
    queued = np.r_[False, k[1:] < u[:-1] + handler[:-1]]
    bad = (r["flags"] & 5) != 0
    for i in np.flatnonzero(bad):
        j = i + 1
        while j < len(r) and queued[j]:
            bad[j] = True
            j += 1
    return u - k, handler, ~bad


def collect(dirs):
    rare = defaultdict(lambda: {"lat": [], "handler": [], "type": []})
    system = defaultdict(lambda: {"lat": [], "block_q": []})
    paired = defaultdict(list)
    twins = []  # A/A: same mode on two cores of a block, quantile difference
    cost = defaultdict(lambda: {"share": [], "shift": [], "n": 0})
    for d in dirs:
        grp, recs = load_block(d)
        block = defaultdict(list)
        for (cpu, mode), v in sorted(recs.items()):
            r = v["r"]
            wait, handler, ok = measure(r, v["ghz"])
            lat = wait + handler
            loc = watched_locate(r)
            mine = r["locate"] == loc if loc is not None else np.full(len(r), grp == "one")
            rare[(grp, mode)]["lat"].append(lat[ok & mine])
            rare[(grp, mode)]["handler"].append(handler[ok & mine])
            rare[(grp, mode)]["type"].append(r["type"][ok & mine])
            others = ok & ~mine
            if grp != "all" or not others.any():
                continue
            q = np.quantile(lat[others], QS)
            system[mode]["lat"].append(lat[others].astype(np.float32))
            system[mode]["block_q"].append(q)
            block[mode].append({"q": q, "seq": r["seq"][others], "wait": wait[others],
                                "warm": r["warm_ns"][others].astype(np.int64)})
        if REF in block:
            ref = block[REF][0]
            for mode, runs in block.items():
                if mode == REF:
                    continue
                paired[mode] += [x["q"] - ref["q"] for x in runs]
                if mode in WARMLESS:
                    continue
                for x in runs:
                    _, ia, ib = np.intersect1d(x["seq"], ref["seq"], assume_unique=True, return_indices=True)
                    excess = x["wait"][ia] - ref["wait"][ib]
                    warm = x["warm"][ia]
                    near = (warm > 0) & (warm < NEAR_NS)
                    far = (warm == 0) | (warm >= FAR_NS)
                    if near.sum() >= 100 and far.sum() >= 100:
                        c = cost[mode]
                        c["share"].append(near.mean())
                        c["shift"].append(np.quantile(excess[near], QS) - np.quantile(excess[far], QS))
                        c["n"] += near.sum()
        twins += [x["q"] - runs[0]["q"] for runs in block.values() for x in runs[1:]]
    return rare, system, paired, twins, cost


def block_ci(parts, q, n=500, seed=1):
    if len(parts) < 5:
        return None
    rng = np.random.default_rng(seed)
    stats = [np.quantile(np.concatenate([parts[i] for i in rng.integers(0, len(parts), len(parts))]), q)
             for _ in range(n)]
    return np.quantile(stats, [0.025, 0.975])


def modes_of(keys):
    known = [m for m in ORDER if m in keys]
    return known + sorted(m for m in keys if m not in known)


def mark(n, q):
    return "" if n * (1 - q) >= MIN_TAIL else "*"


def report_rare(rare):
    for grp, arch in (("one", "B: quiet pair only"), ("all", "A: full feed")):
        modes = modes_of({m for g, m in rare if g == grp})
        if not modes:
            continue
        print(f"\n=== Rare handler, architecture {arch}")
        print(f"  {'method':<18} {'packets':>8} | {'handler, ns: p50':>19} {'p99':>6} {'p999':>6} | "
              f"{'latency, us: p50':>18} {'p99':>7} {'p999':>7}  [95% CI p999]")
        for m in modes:
            a = rare[(grp, m)]
            lat, h = np.concatenate(a["lat"]) / 1e3, np.concatenate(a["handler"])
            if not len(lat):
                continue
            hq, lq = np.quantile(h, QS), np.quantile(lat, QS)
            ci = block_ci([x / 1e3 for x in a["lat"]], .999)
            ci_s = f"[{ci[0]:.1f} – {ci[1]:.1f}]" if ci is not None else ""
            print(f"  {m:<18} {len(lat):>8} | {hq[0]:>19.0f} {hq[1]:>6.0f} {hq[2]:>5.0f}{mark(len(h), .999):1} | "
                  f"{lq[0]:>18.2f} {lq[1]:>7.2f} {lq[2]:>6.2f}{mark(len(lat), .999):1}  {ci_s}")
        if (grp, REF) in rare:
            tail_types(rare[(grp, REF)])


def tail_types(a):
    """What the handler tail is made of: type of the packet's first message, share among all and beyond p99."""
    h, t = np.concatenate(a["handler"]), np.concatenate(a["type"])
    if len(h) < 100:
        return
    tail = h >= np.quantile(h, .99)
    kinds = sorted(set(t[tail]), key=lambda x: -(t[tail] == x).sum())
    cells = ", ".join(f"{chr(x)} {100 * (t == x).mean():.0f}% → {100 * (t[tail] == x).mean():.0f}%" for x in kinds)
    print(f"  handler tail ({REF}), message type: share among all → beyond p99: {cells}")


def report_system(system, paired, twins, cost):
    print("\n=== System: packets of the other pairs, architecture A; latency, us")
    print(f"  {'method':<18} {'packets':>9} {'p50':>6} {'p99':>6} {'p999':>6} {'max':>8} | "
          f"p999 per block: median [min – max]")
    for m in modes_of(system):
        s = system[m]
        lat = np.concatenate(s["lat"]) / 1e3
        q = np.quantile(lat, QS)
        per = np.array([b[2] for b in s["block_q"]]) / 1e3
        print(f"  {m:<18} {len(lat):>9} {q[0]:>6.2f} {q[1]:>6.2f} {q[2]:>6.2f} {lat.max():>8.1f} | "
              f"{np.median(per):.2f} [{per.min():.2f} – {per.max():.2f}]")

    if paired:
        print(f"\n  Within a block against {REF}, ns: median difference over blocks (blocks where higher / total)")
        for m in modes_of(paired):
            d = np.array(paired[m])
            print(f"  {m:<18} p50 {np.median(d[:, 0]):+7.0f} ({(d[:, 0] > 0).sum()}/{len(d)})   "
                  f"p99 {np.median(d[:, 1]):+7.0f} ({(d[:, 1] > 0).sum()}/{len(d)})   "
                  f"p999 {np.median(d[:, 2]):+7.0f} ({(d[:, 2] > 0).sum()}/{len(d)})")
    if twins:
        d = np.abs(np.array(twins))
        print(f"  A/A, same method on two cores of a block ({len(d)} pairs): "
              f"|Δp99| median {np.median(d[:, 1]):.0f} ns, |Δp999| median {np.median(d[:, 2]):.0f}, "
              f"max {d[:, 2].max():.0f} ns")

    if cost:
        print(f"\n  Warm-up cost for the other pairs. Packet wait with the method minus with {REF} (same packet), "
              f"then the quantile over packets where warm-up\n  ended < {NEAR_NS // 1000} us before the end of "
              f"handling, minus the same quantile where ≥ {FAR_NS // 1000} us. Median over blocks, ns")
        for m in modes_of(cost):
            c = cost[m]
            d = np.array(c["shift"])
            print(f"  {m:<18} packets near warm-up {100 * np.median(c['share']):5.2f}%   "
                  f"p50 {np.median(d[:, 0]):+5.0f}   p99 {np.median(d[:, 1]):+5.0f}   "
                  f"p999 {np.median(d[:, 2]):+5.0f}{mark(c['n'] / len(d), .999)}   ({len(d)} blocks)")

def main():
    dirs = expand(sys.argv[1:])
    if not dirs:
        sys.exit("no blocks")
    rare, system, paired, twins, cost = collect(dirs)
    print(f'{len(dirs)} blocks. Latency: "kernel → end of handler". *: fewer than {MIN_TAIL} points beyond the '
          f"quantile, the value is descriptive. CI: bootstrap over blocks.")
    report_rare(rare)
    if system:
        report_system(system, paired, twins, cost)


if __name__ == "__main__":
    main()
