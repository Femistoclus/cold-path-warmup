#!/usr/bin/env python3
"""Summary of the dis.sh series: targeted disruption.

A. Penalty of each disruptor = q(cell) − q(base) of the same run, q = p50, p99, p999 (p999 only with ≥ 10
   points beyond it); median over runs (range); minus its control; the same on bench-null; recovery call.
B. Counter signature: target counter rises by ≥ 70% of the reference, others stay ≤ base + max(3, 10% ref).
C. Dose curves: penalty and key counters by dose, half-penalty dose, ns per event.
D. Additivity: penalty(A+B) − penalty(A) − penalty(B), p50 and trimmed mean; synthetic C3 / C6.
E. Real C-states + disruption after sleep. G. Controls: settle, A/A, order of a pair, drift A → A2.
Trials whose dis_ns exceeds the cell median by > 1 µs are dropped (interrupts); runs with hp=0 are skipped.

Usage: python3 disrupt.py [results dir]
"""
import csv
import glob
import os
import re
import statistics
import sys
from collections import defaultdict

from analyze import DISCARD, quantile

FIXED_COLS = {"trial", "cond", "dt_ms", "call", "ns", "late_us", "wakes", "warm_us", "pre_ns", "gap_us", "dis", "dis_ns"}
QS = ((.5, "p50"), (.99, "p99"), (.999, "p999"))

# Reference counts of the first call after C6 (plan, section 4); dsb_uops falls 421 → 23, so it is checked as a drop.
REF = {"l1_miss": 68, "l2_miss": 26, "l3_miss": 46, "dtlb_walk": 14, "dtlb_stlb_hit": 14, "itlb_walk": 14,
       "itlb_stlb_hit": 14, "ic_miss": 334, "code_l2_miss": 262, "dsb_uops": 398, "baclears": 92, "br_miss": 32,
       "br_miss_cond": 32}
FALLS = {"dsb_uops"}
TARGET = {
    "d1:64k": ["l1_miss"], "d1:24k": ["l1_miss"], "l1dflush": ["l1_miss"], "d2:1m": ["l1_miss", "l2_miss"],
    "d3:64m": ["l1_miss", "l2_miss", "ic_miss", "code_l2_miss"], "clf:d": ["l1_miss", "l3_miss"],
    "clf:c": ["ic_miss", "code_l2_miss"], "dtlb:128": ["dtlb_stlb_hit"], "dtlb:3072": ["dtlb_walk"],
    "itlb:256": ["itlb_stlb_hit"], "itlb:3072": ["itlb_walk"], "tlbflush": ["dtlb_walk", "itlb_walk"],
    "line:16k": ["dsb_uops"], "line:64k": ["ic_miss", "dsb_uops"], "line:128k": ["ic_miss", "dsb_uops"],
    "line:1m": ["ic_miss", "code_l2_miss", "dsb_uops"], "jumpy:64k": ["ic_miss", "dsb_uops"],
    "jumpy:128k": ["ic_miss", "dsb_uops", "baclears"], "jumpy:256k": ["ic_miss", "dsb_uops", "baclears"],
    "dirn:16k": [], "dirw:64k": ["ic_miss", "dsb_uops", "br_miss"], "ibpb": [],
}
# Side effects that are expected (own control or an open question of H3–H7) and not held against the signature.
FREE = {
    "d3:64m": ["l3_miss", "dsb_uops"], "clf:d": ["l2_miss"], "clf:c": ["dsb_uops"], "dtlb:3072": ["l1_miss", "l2_miss"],
    "itlb:256": ["dsb_uops"], "itlb:3072": ["l1_miss", "l2_miss", "dsb_uops"], "tlbflush": ["dsb_uops"],
    "line:64k": ["code_l2_miss"], "line:128k": ["code_l2_miss"], "jumpy:64k": ["baclears", "br_miss", "br_miss_cond"],
    "jumpy:128k": ["br_miss", "br_miss_cond"], "jumpy:256k": ["br_miss", "br_miss_cond"],
    "dirn:16k": ["br_miss", "br_miss_cond"], "dirw:64k": ["baclears", "br_miss_cond"],
    "ibpb": ["br_miss", "br_miss_cond", "baclears"],
}
CONTROL = {"dtlb:3072": "d1:24k", "itlb:3072": "d1:24k", "jumpy:64k": "line:64k", "jumpy:128k": "line:128k",
           "jumpy:256k": "line:256k", "dirw:64k": "jumpy:64k", "ibpb": "wr0", "l1dflush": "wr0:10b",
           "tlbflush": "mprot1", "wr0": "sys", "wr0:10b": "sys", "mprot1": "sys"}
SYNTH = {"line:1m+d2:1m+tlbflush": ("synth. C3", 432),
         "ibpb+jumpy:256k+dirw:64k+line:1m+d2:1m+tlbflush": ("synth. C6", 963)}
KEY_COUNTERS = {"d": ["l1_miss", "l2_miss"], "line": ["ic_miss", "code_l2_miss", "dsb_uops"],
                "jumpy": ["baclears", "br_miss", "ic_miss"], "dtlb": ["dtlb_walk", "dtlb_stlb_hit"],
                "itlb": ["itlb_walk", "itlb_stlb_hit"], "dirn": ["br_miss_cond", "br_miss"],
                "clf:d": ["l1_miss", "l3_miss"], "clf:c": ["ic_miss", "code_l2_miss"]}


def label(r):
    dis = r.get("dis", "-")
    if dis == "-":
        return r["cond"]
    return dis if r["cond"] == "dis" else f"{r['cond']}+{dis}"


def load(path):
    """(cell, Δt) → {ns: first call, curve: [ns of calls 1..8 per trial], dis_ns, rejected, counter: values}."""
    raw = defaultdict(lambda: {"dis_ns": [], "calls": [], "cnt": defaultdict(list)})
    cur = None
    with open(path) as f:
        for r in csv.DictReader(f):
            if int(r["trial"]) < DISCARD:
                cur = None
                continue
            if r["call"] == "1":
                c = raw[(label(r), int(r["dt_ms"]))]
                c["dis_ns"].append(float(r.get("dis_ns", 0)))
                cur = [float(r["ns"])]
                c["calls"].append(cur)
                for k, v in r.items():
                    if k not in FIXED_COLS:
                        c["cnt"][k].append(float(v))
            elif cur is not None:
                cur.append(float(r["ns"]))
    out = {}
    for key, c in raw.items():
        limit = statistics.median(c["dis_ns"]) + 1000
        keep = [i for i, d in enumerate(c["dis_ns"]) if d <= limit]
        cell = {"ns": [c["calls"][i][0] for i in keep], "curve": [c["calls"][i] for i in keep],
                "dis_ns": [c["dis_ns"][i] for i in keep], "rejected": 1 - len(keep) / len(c["dis_ns"])}
        cell.update({k: [v[i] for i in keep] for k, v in c["cnt"].items()})
        out[key] = cell
    return out


def runs(root, pat):
    """[(run name, data)] for results/*-<pat>/calls.csv; runs without huge pages (hp=0) are skipped."""
    found = []
    for p in sorted(glob.glob(os.path.join(root, f"*-{pat}", "calls.csv"))):
        d = os.path.dirname(p)
        log = os.path.join(d, "bench.log")
        if os.path.exists(log) and "hp=0" in open(log).read():
            print(f"  (skipped {os.path.basename(d)}: hp=0)")
            continue
        found.append((os.path.basename(d), load(p)))
    return found


def q(xs, p):
    """Quantile, or None when fewer than ~10 points lie beyond it."""
    return quantile(xs, p) if xs and len(xs) * (1 - p) >= 9.5 else None


def pen(d, cell, p=.5, ref="base", dt=0, ref_dt=0):
    a, b = d.get((cell, dt)), d.get((ref, ref_dt))
    if not a or not b:
        return None
    qa, qb = q(a["ns"], p), q(b["ns"], p)
    return None if qa is None or qb is None else qa - qb


def agg(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return "—"
    m = statistics.median(v)
    return f"{m:.0f}" if len(v) == 1 else f"{m:.0f} ({min(v):.0f}…{max(v):.0f})"


def med(vals):
    v = [x for x in vals if x is not None]
    return statistics.median(v) if v else None


KIND_ORDER = ["sys", "wr0", "mprot1", "d", "l1dflush", "clf:d", "clf:c", "dtlb", "itlb", "tlbflush", "line", "jumpy",
              "dirn", "dirw", "ibpb"]


def cell_order(c):
    k = kind_of(c)
    return ("+" in c, KIND_ORDER.index(k) if k in KIND_ORDER else len(KIND_ORDER), dose_of(c))


def cells_of(data):
    """Disruptor cells of Δt = 0 except base: singles by kind and dose, then joint cells."""
    seen = {c for _, d in data for (c, dt) in d if dt == 0 and c not in ("hot", "base")}
    return sorted(seen, key=cell_order)


def mean_counters(data, cell, dt=0):
    out = {}
    for _, d in data:
        for k, v in d.get((cell, dt), {}).items():
            if k not in ("ns", "curve", "dis_ns", "rejected") and v:
                out[k] = statistics.mean(v)
    return out


def recovery_call(d, cell):
    """First call after the disruption whose p50 is within 10 ns of base."""
    a, b = d.get((cell, 0)), d.get(("base", 0))
    if not a or not b:
        return None
    for j in range(8):
        xa = [t[j] for t in a["curve"] if len(t) > j]
        xb = [t[j] for t in b["curve"] if len(t) > j]
        if xa and xb and quantile(xa, .5) - quantile(xb, .5) < 10:
            return j + 1
    return 9


def part_a(root):
    data = runs(root, "dA-r*")
    if not data:
        return
    null = runs(root, "dAnull-r*")
    print(f"A. Disruption penalty vs base of the same run, ns: median over {len(data)} runs (range)\n")
    print(f"  base − hot, p50: {agg([pen(d, 'base', ref='hot') for _, d in data])} (expected ≤ 15)")
    print(f"  {'disruptor':<14}{'p50':>16}{'p99':>16}{'p999':>16}{'− control p50':>28}{'bench-null p50':>16}"
          f"{'dis, µs':>10}{'rej.':>7}{'recov.':>8}")
    for c in cells_of(data):
        cols = [agg([pen(d, c, p) for _, d in data]) for p, _ in QS]
        ctl = CONTROL.get(c)
        minus = agg([None if pen(d, c) is None or pen(d, ctl) is None else pen(d, c) - pen(d, ctl)
                     for _, d in data]) + f" ({ctl})" if ctl else ""
        dis = med([statistics.median(d[(c, 0)]["dis_ns"]) / 1000 for _, d in data if (c, 0) in d])
        rej = med([100 * d[(c, 0)]["rejected"] for _, d in data if (c, 0) in d])
        back = med([recovery_call(d, c) for _, d in data])
        print(f"  {c:<14}" + "".join(f"{x:>16}" for x in cols) + f"{minus:>28}{agg([pen(d, c) for _, d in null]):>16}"
              f"{dis if dis is not None else float('nan'):10.1f}{rej if rej is not None else 0:6.1f}%"
              f"{'—' if back is None else f'{back:.0f}':>8}")
    d3 = runs(root, "dA-d3")
    if d3:
        print(f"  d3:64m (alone, 1000 trials): p50 {agg([pen(d, 'd3:64m') for _, d in d3])}")
    print()


def passport(data, title):
    base = mean_counters(data, "base")
    if not base:
        return
    print(f"{title}: mean of the 1st call minus base; target ≥ 70% of reference, off-target ≤ max(3; 10% of reference)\n")
    passed = total = 0
    for c in cells_of(data):
        m = mean_counters(data, c)
        rise = {k: (base[k] - m[k] if k in FALLS else m[k] - base[k]) for k in REF if k in m and k in base}
        if c not in TARGET:
            moved = [f"{k} {v:+.0f}" for k, v in rise.items() if abs(v) > max(3, .1 * REF[k])]
            print(f"  {c:<14}      shifts: {', '.join(moved) or '—'}")
            continue
        notes, ok = [], True
        if any(k not in rise for k in TARGET[c]):
            print(f"  {c:<14} ?    no target counters: {', '.join(k for k in TARGET[c] if k not in rise)}")
            continue
        for k in TARGET[c]:
            if k in rise:
                good = rise[k] >= .7 * REF[k]
                ok &= good
                notes.append(f"{k} {rise[k]:+.0f}{'' if good else ' (low)'}")
        side = [f"{k} {v:+.0f}" for k, v in rise.items()
                if k not in TARGET[c] and k not in FREE.get(c, []) and v > max(3, .1 * REF[k])]
        ok &= not side
        total += 1
        passed += ok
        print(f"  {c:<14} {'yes' if ok else 'NO '}  target: {', '.join(notes) or '—'}"
              + (f"; off-target: {', '.join(side)}" if side else ""))
    print(f"  signature matched: {passed} of {total} (H1: ≥ 10 of 14)\n")


def part_b(root):
    data = [x for g in "abcde" for x in runs(root, f"dB-{g}")]
    if data:
        passport(data, "B. Disruption signature (bench)")


def dose_of(c):
    v = c.split("+")[0].rsplit(":", 1)[-1]
    scale = {"k": 1024, "m": 1 << 20}.get(v[-1].lower(), 1)
    try:
        return float(v.rstrip("kKmM")) * scale
    except ValueError:
        return 0.0


def kind_of(c):
    name = c.split(":")[0]
    return "d" if name in ("d1", "d2", "d3") else ("clf:" + c.split(":")[1][:1] if name == "clf" else name)


def slope(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    mx, my = statistics.mean(p[0] for p in pts), statistics.mean(p[1] for p in pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    return sum((x - mx) * (y - my) for x, y in pts) / sxx if sxx else None


def part_c(root):
    names = sorted({re.sub(r"^.*-dC-([A-Z]+)-.*$", r"\1", os.path.basename(os.path.dirname(p)))
                    for p in glob.glob(os.path.join(root, "*-dC-*", "calls.csv"))})
    if not names:
        return
    print("C. Dose curves: p50 penalty vs base (median over runs), counters minus base, disruption duration\n")
    for name in names:
        data = runs(root, f"dC-{name}-r*")
        cnt = [x for g in "bcde" for x in runs(root, f"dC-{name}-{g}")]
        cells = sorted(cells_of(data), key=dose_of)
        if not cells:
            continue
        keys = KEY_COUNTERS.get(kind_of(cells[0]), [])
        base = mean_counters(cnt, "base")
        print(f"  {name}: {'dose':<12}{'p50 penalty':>16}" + "".join(f"{k:>15}" for k in keys) + f"{'dis, µs':>10}")
        pens, events = [], []
        for c in cells:
            p = med([pen(d, c) for _, d in data])
            m = mean_counters(cnt, c)
            delta = [m[k] - base[k] if k in m and k in base else None for k in keys]
            dis = med([statistics.median(d[(c, 0)]["dis_ns"]) / 1000 for _, d in data if (c, 0) in d])
            mark = "!" if dis is not None and dis > 100 else " "
            pens.append(p)
            events.append(delta[0] if delta else None)
            print(f"  {'':<{len(name) + 2}}{c:<12}{agg([pen(d, c) for _, d in data]):>16}"
                  + "".join(f"{x:15.1f}" if x is not None else f"{'—':>15}" for x in delta)
                  + f"{dis if dis is not None else float('nan'):9.1f}{mark}")
        valid = [(c, p) for c, p in zip(cells, pens) if p is not None]
        if valid:
            top = max(p for _, p in valid)
            half = next((c for c, p in valid if p >= top / 2), None)
            line = f"  {'':<{len(name) + 2}}plateau {top:.0f} ns, half penalty at {half}"
            per_event = slope(events, pens)
            if name.startswith("CLF"):
                per_event = slope([dose_of(c) for c in cells], pens)
                line += f"; slope {per_event:.1f} ns per line" if per_event is not None else ""
            elif per_event is not None and keys:
                line += f"; {per_event:.2f} ns per {keys[0]} event"
            print(line)
        print()


def trimmed_mean(xs, cut=.1):
    s = sorted(xs)
    k = int(len(s) * cut)
    return statistics.mean(s[k:len(s) - k]) if len(s) > 2 * k else float("nan")


def tpen(d, cell):
    a, b = d.get((cell, 0)), d.get(("base", 0))
    return trimmed_mean(a["ns"]) - trimmed_mean(b["ns"]) if a and b else None


def additivity(data):
    print(f"  {'joint':<52}{'p50: A+B − ΣA':>18}{'sum of singles':>17}{'trimmed mean':>15}  significant")
    for c in cells_of(data):
        parts = [x for x in c.split("+") if x != "base"]
        if len(parts) < 2:
            continue
        rows, sums, trims = [], [], []
        for _, d in data:
            single = [pen(d, x) for x in parts]
            if pen(d, c) is None or None in single:
                continue
            rows.append(pen(d, c) - sum(single))
            sums.append(sum(single))
            trims.append(tpen(d, c) - sum(tpen(d, x) for x in parts))
        if not rows:
            continue
        m = statistics.median(rows)
        spread = max(rows) - min(rows)
        sig = abs(m) > max(15, .15 * abs(statistics.median(sums))) and abs(m) > spread
        print(f"  {c:<52}{agg(rows):>18}{agg(sums):>17}{agg(trims):>15}  {'yes' if sig else 'no'}")
    for c, (name, real) in SYNTH.items():
        v = med([pen(d, c) for _, d in data])
        if v is not None:
            print(f"  {name} = {c}: {v:.0f} ns vs real {real} ({100 * (v - real) / real:+.0f}%, expected ± 15%)")


def part_d(root):
    data = runs(root, "dD-r*")
    if not data:
        return
    print(f"D. Additivity: penalty(A+B) − Σ penalty(parts), ns; median over {len(data)} runs (range)\n")
    additivity(data)
    print()
    cnt = [x for g in "abcde" for x in runs(root, f"dD-{g}")]
    if cnt:
        passport(cnt, "D. Signature of single and joint disruptions")


def part_e(root):
    shown = False
    for pol in ("1", "2", "3", "8"):
        data = runs(root, f"dE{pol}-r*")
        if not data:
            continue
        if not shown:
            print("E. Real sleep Δt = 10 ms (+ disruption after it): 1st-call penalty vs hot, ns\n")
            shown = True
        cells = sorted({c for _, d in data for (c, dt) in d if dt == 10})
        print(f"  policy {pol}: " + "; ".join(
            f"{c} {agg([pen(d, c, ref='hot', dt=10) for _, d in data])}" for c in cells))
    c3 = runs(root, "dE3-r*")
    c6 = runs(root, "dE8-r*")
    if c3 and c6:
        a = med([pen(d, "none+ibpb+jumpy:256k+dirw:64k", ref="hot", dt=10) for _, d in c3])
        b = med([pen(d, "none", ref="hot", dt=10) for _, d in c6])
        if a is not None and b is not None:
            print(f"  C3 + predictor disruption {a:.0f} ns vs C6 {b:.0f} ns ({100 * (a - b) / b:+.0f}%, expected ± 15%)")
    if shown:
        print()


def part_g(root):
    settle = {s: runs(root, f"dG-settle{s}-r*") for s in ("0", "1000", "10000")}
    if any(settle.values()):
        print("G. Settle gap 0 / 1 / 10 µs: p50 penalty vs base, ns (expected: difference ≤ 5)")
        print(f"  {'':<12}{'0':>16}{'1 µs':>16}{'10 µs':>16}")
        for c in ("d2:1m", "line:64k", "jumpy:128k", "base+base"):
            print(f"  {c:<12}" + "".join(f"{agg([pen(d, c) for _, d in settle[s]]):>16}" for s in settle))
        aa = settle["1000"]
        if aa:
            print("  A/A (base+base − base): " + ", ".join(
                f"{n} {agg([pen(d, 'base+base', p) for _, d in aa])}" for p, n in QS))
    perm = runs(root, "dG-perm-r*")
    if perm:
        print("  Order within a pair:")
        additivity(perm)
    a1, a2 = runs(root, "dA-r*"), runs(root, "dA2-r*")
    if a1 and a2:
        drift = []
        for c in cells_of(a1):
            x, y = med([pen(d, c) for _, d in a1]), med([pen(d, c) for _, d in a2])
            if x is not None and y is not None and abs(x) > 20:
                drift.append(f"{c} {100 * (y - x) / x:+.0f}%")
        print("  Drift A → A2 (penalty > 20 ns): " + ", ".join(drift))
    print()


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "results"
    part_a(root)
    part_b(root)
    part_c(root)
    part_d(root)
    part_e(root)
    part_g(root)


if __name__ == "__main__":
    main()
