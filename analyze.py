#!/usr/bin/env python3
"""Metrics of a bench run.

Everything is measured on the first call after the pause; calls 2–8 give the warm-up curve.

Main metrics:
  - Penalty = p50(mode) − p50(hot), ns, with a bootstrap 95% CI.
  - Event → end of first call = wake-up lateness + first call, µs: what the system pays from the
    moment the event should have started being processed.
Auxiliary:
  - Penalty ratio = p50(mode) / p50(hot); shown only next to the absolute penalty.
  - Share of the penalty removed by warm-up = (empty − shadow) / (empty − hot), and the same for
    spin / spinshadow; printed only if the penalty without warm-up is reliably above 50 ns.
  - Warm-up cost: share of one core over the pause.
Take timings from --no-counters runs: rdpmc needs no syscall, but the window with counters is still
slightly longer, so compare "hot" against a run without counters.
p99 marked with * has fewer than 10 trials beyond it (always the case at ~250 trials per cell); p999 is shown
only when a cell has at least 10 thousand trials.

Usage:
  python3 analyze.py results/<run>/calls.csv
  python3 analyze.py results/<A>/calls.csv results/<B>/calls.csv   # also compare two runs
"""
import csv
import math
import os
import random
import sys
from collections import defaultdict

DISCARD = 50  # leading trials dropped: the harness itself is warming up
BOOT = 400    # bootstrap resamples
REF = ("hot", "cold", "sys")                  # modes without a pause
PAUSE = ("spin", "spinshadow", "spinpf", "none", "empty", "shadow", "prewake", "pfwake", "pfevent")   # modes with pause Δt
NAME = {"hot": "hot", "cold": "64 MB data eviction", "sys": "kernel entry (getppid)",
        "spin": "spin-wait (spin)", "spinshadow": "spin + shadow every T", "none": "sleep, no warm-up",
        "empty": "empty wake-ups", "shadow": "shadow execution",
        "prewake": "warm-up before event", "pfwake": "prefetch before event",
        "pfevent": "prefetch at event", "spinpf": "spin + prefetch every T"}


def quantile(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


def p99(xs, digits=3):
    """p99 with * if fewer than 10 points lie beyond it (then it is descriptive only)."""
    star = "*" if len(xs) * 0.01 < 10 else ""
    return f"{quantile(xs, .99):.{digits}f}{star}"


def cycle_scale(path):
    """ns per core cycle when the run counted cycles only (group t, used on AMD where TSC ticks in ~10 ns steps):
    then time comes from cycles; the scale is the average over the run, where TSC steps average out."""
    with open(path) as f:
        header = f.readline().strip().split(",")
    if "cycles" not in header or "ref_cycles" in header or "l1d_miss" in header:
        return None
    total_ns = total_cycles = 0.0
    with open(path) as f:
        for r in csv.DictReader(f):
            total_ns += float(r["ns"])
            total_cycles += float(r["cycles"])
    return total_ns / total_cycles if total_cycles else None


def p999(xs, digits=3):
    star = "*" if len(xs) * 0.001 < 10 else ""
    return f"{quantile(xs, .999):.{digits}f}{star}"


def load(path):
    """first[(mode, Δt)]: first-call times, µs;
    curve[(mode, Δt)][call number]: times of all calls;
    extra[(mode, Δt)][field]: other first-call fields (lateness, pre_ns, counters...)."""
    first = defaultdict(list)
    curve = defaultdict(lambda: defaultdict(list))
    extra = defaultdict(lambda: defaultdict(list))
    ns_per_cycle = cycle_scale(path)
    with open(path) as f:
        for r in csv.DictReader(f):
            if int(r["trial"]) < DISCARD:
                continue
            # Disruptor cells (column dis) are kept apart from plain modes; disrupt.py reports them.
            dis = r.get("dis", "-")
            key = (r["cond"] if dis == "-" else f"{r['cond']}+{dis}", int(r["dt_ms"]))
            us = (float(r["cycles"]) * ns_per_cycle if ns_per_cycle else float(r["ns"])) / 1000
            call = int(r["call"])
            curve[key][call].append(us)
            if call == 1:
                first[key].append(us)
                for k, v in r.items():
                    if k not in ("trial", "cond", "dt_ms", "call", "ns", "dis"):
                        extra[key][k].append(float(v))
    return first, curve, extra


def diff(x, hot, q):
    """Penalty at quantile q, µs."""
    return quantile(x, q) - quantile(hot, q)


def removed(hot, empty, shadow, p):
    h, e, s = quantile(hot, p), quantile(empty, p), quantile(shadow, p)
    return (e - s) / (e - h) if e > h else float("nan")


def ci(stat, samples):
    """Bootstrap 95% CI of stat(*samples)."""
    rnd = random.Random(0)
    vals = [stat(*[rnd.choices(x, k=len(x)) for x in samples]) for _ in range(BOOT)]
    vals = sorted(v for v in vals if not math.isnan(v))
    if not vals:
        return float("nan"), float("nan")
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def penalty_ns(x, hot):
    """p50 penalty as a table cell: 'ns [95% CI]'."""
    v = diff(x, hot, .5)
    lo, hi = ci(lambda a, b: diff(a, b, .5), [x, hot])
    return f"{1000 * v:6.0f} [{1000 * lo:5.0f} – {1000 * hi:5.0f}]"


def report(path):
    first, curve, extra = load(path)
    out = []
    p = out.append
    dts = sorted({dt for (c, dt) in first if c in PAUSE})
    hot = first.get(("hot", 0))
    n_cell = max((len(v) for v in first.values()), default=0)
    wide = n_cell * 0.001 >= 10  # p999 column only when it can rest on 10 points
    h999 = f" {'p999, us':>10}" if wide else ""

    p(f"Run: {path}")
    p(f"Trials per cell: ~{n_cell} (first {DISCARD} trials of the run discarded).\n")

    p(f"No pause, 1st call:          {'p50, us':>9} {'p90, us':>9} {'p99, us':>9}{h999} | p50 penalty, ns [95% CI]")
    for c in REF:
        x = first.get((c, 0))
        if x:
            pen = penalty_ns(x, hot) if hot and c != "hot" else ""
            c999 = f" {p999(x):>10}" if wide else ""
            p(f"  {NAME[c]:<26} {quantile(x, .5):9.3f} {quantile(x, .9):9.3f} {p99(x):>9}{c999} | {pen}")
    pre = {k: v["pre_ns"] for k, v in extra.items() if "pre_ns" in v}
    if pre:
        med = {k: quantile(v, .5) for k, v in pre.items()}
        p(f"  last hot call before the pause (pre_ns), p50 across modes: "
          f"{min(med.values()):.0f}–{max(med.values()):.0f} ns")
    p("")

    for dt in dts:
        p(f"Pause Δt = {dt} ms, 1st call:")
        p(f"  {'mode':<26} {'p50, us':>9} {'p90, us':>9} {'p99, us':>9}{h999} {'ratio':>5} | "
          f"{'p50 penalty, ns [95% CI]':>24} | {'p50 lateness, us':>18} | event → end of 1st call p50 / p90 / p99"
          f"{' / p999' if wide else ''}, us")
        for c in PAUSE:
            x = first.get((c, dt))
            if not x:
                continue
            late = extra[(c, dt)]["late_us"]
            e2e = [l + f for l, f in zip(late, x)]
            ratio = f"{quantile(x, .5) / quantile(hot, .5):5.1f}" if hot else "  —"
            pen = penalty_ns(x, hot) if hot else "—"
            c999 = f" {p999(x):>10}" if wide else ""
            e999 = f" / {p999(e2e, 1)}" if wide else ""
            p(f"  {NAME[c]:<26} {quantile(x, .5):9.3f} {quantile(x, .9):9.3f} {p99(x):>9}{c999} {ratio} | {pen:>24} | "
              f"{quantile(late, .5):18.1f} | {quantile(e2e, .5):.1f} / {quantile(e2e, .9):.1f} / {p99(e2e, 1)}{e999}")
        p("")

    # Each warm-up mode against its own no-warm-up baseline.
    for base, warm, title in (("empty", "shadow", "sleep: (empty − shadow) / (empty − hot)"),
                              ("spin", "spinshadow", "spin: (spin − spin+shadow) / (spin − hot)")):
        if not (hot and any((warm, dt) in first and (base, dt) in first for dt in dts)):
            continue
        p(f"Share of the penalty removed by warm-up, {title}, %")
        for dt in dts:
            e, s = first.get((base, dt)), first.get((warm, dt))
            if not (e and s):
                continue
            lo_den, _ = ci(lambda e_, h: diff(e_, h, .5), [e, hot])
            wakes = mean(extra[(warm, dt)]["wakes"])
            cost = 100 * mean(extra[(warm, dt)]["warm_us"]) / (dt * 1000) if dt else float("nan")
            if not lo_den > 0.05:
                p(f"  Δt = {dt} ms: penalty without warm-up not reliably above 50 ns "
                  f"(CI lower bound {1000 * lo_den:.0f} ns), nothing to remove")
                continue
            row = []
            for q in (.5, .9):
                v = removed(hot, e, s, q)
                lo, hi = ci(lambda h, e_, s_, q=q: removed(h, e_, s_, q), [hot, e, s])
                row.append(f"{100 * v:4.0f} [{100 * lo:4.0f} – {100 * hi:4.0f}]")
            p(f"  Δt = {dt} ms: p50 {row[0]}, p90 {row[1]}; warm-ups {wakes:.0f}, cost {cost:.2f}% of a core")
        p("")

    if dts:
        dt = dts[-1]
        p(f"Warm-up curve at Δt = {dt} ms: p50 by call number after the pause, us")
        keys = [("hot", 0)] + [(c, dt) for c in PAUSE]
        p(f"  {'call':<26}" + "".join(f"{i:>7}" for i in range(1, 9)))
        for c, d in keys:
            if (c, d) in curve:
                p(f"  {NAME[c]:<26}" + "".join(f"{quantile(curve[(c, d)][i], .5):7.3f}" for i in range(1, 9)))
        p("")

        cnt_fields = [k for k in extra[("hot", 0)]
                      if k not in ("late_us", "wakes", "warm_us", "pre_ns", "gap_us", "dis_ns", "nb_work")]
        if cnt_fields:
            p(f"CPU counters, 1st call, mean (Δt = {dt} ms)")
            freq = "cycles" in cnt_fields and "ref_cycles" in cnt_fields
            p(f"  {'mode':<26}" + "".join(f"{k:>11}" for k in cnt_fields) + (f"  {'cycles/ref':>14}" if freq else ""))
            for c, d in [(c, 0) for c in REF] + [(c, dt) for c in PAUSE]:
                if (c, d) in extra:
                    e = extra[(c, d)]
                    p(f"  {NAME[c]:<26}" + "".join(f"{mean(e[k]):11.0f}" for k in cnt_fields)
                      + (f"  {sum(e['cycles']) / max(1, sum(e['ref_cycles'])):14.2f}" if freq else ""))
            p("")

        x_none = first.get(("none", dt))
        if hot and x_none:
            late = extra[("none", dt)]["late_us"]
            e2e = [l + f for l, f in zip(late, x_none)]
            line = (f"Summary: after {dt} ms of sleep the first call is {1000 * diff(x_none, hot, .5):.0f} ns slower "
                    f"than hot at p50 ({quantile(x_none, .5) / quantile(hot, .5):.1f}x); event → end of first call "
                    f"{quantile(e2e, .5):.1f} us at p50.")
            x_spin = first.get(("spin", dt))
            if x_spin:
                line += f" With a spin-wait the penalty is {1000 * diff(x_spin, hot, .5):.0f} ns."
            p(line)
    return "\n".join(out), first, curve


def compare(fa, fb):
    out = ["Two runs compared: 1st-call p50, us"]
    out.append(f"  {'mode':<20} {'Δt':>6} {'A':>8} {'B':>8} {'diff':>9}")
    worst = 0.0
    for key in sorted(set(fa) & set(fb), key=lambda k: (k[1], k[0])):
        a, b = quantile(fa[key], .5), quantile(fb[key], .5)
        d = 100 * (b - a) / a
        worst = max(worst, abs(d))
        out.append(f"  {key[0]:<20} {key[1]:>6} {a:8.3f} {b:8.3f} {d:8.1f}%")
    out.append(f"Largest difference: {worst:.1f}% — {'stable (< 5%)' if worst < 5 else 'unstable (≥ 5%), check the noise'}")
    return "\n".join(out)


def plot(path, first, curve):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(no matplotlib, plot skipped)")
        return
    dts = sorted({dt for (c, dt) in first if c in PAUSE and dt > 0})  # log x axis
    if not dts:
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))
    colors = {"spin": "tab:blue", "spinshadow": "tab:cyan", "none": "tab:red", "empty": "tab:orange", "shadow": "tab:green",
              "prewake": "tab:purple", "pfwake": "tab:brown", "pfevent": "tab:pink", "spinpf": "tab:olive"}
    for c in PAUSE:
        xs = [dt for dt in dts if (c, dt) in first]
        if xs:
            ax1.plot(xs, [quantile(first[(c, dt)], .5) for dt in xs], "o-", color=colors.get(c), label=f"{NAME[c]}, p50")
            ax1.plot(xs, [quantile(first[(c, dt)], .9) for dt in xs], "--", color=colors.get(c), alpha=.5, label=f"{NAME[c]}, p90")
    for c, style in (("hot", ":"), ("cold", "-."), ("sys", "--")):
        if (c, 0) in first:
            ax1.axhline(quantile(first[(c, 0)], .5), linestyle=style, color="k", alpha=.6, label=f"{NAME[c]}, p50")
    ax1.set_xscale("log")
    ax1.set_xticks(dts)
    ax1.set_xticklabels([str(dt) for dt in dts])
    ax1.set_xlabel("pause Δt, ms")
    ax1.set_ylabel("1st call time, µs")
    ax1.set_title("First call after the pause")
    ax1.legend(fontsize=7)
    ax1.grid(alpha=.3)

    dt = dts[-1]
    calls = range(1, 9)
    for c in PAUSE:
        if (c, dt) in curve:
            ax2.plot(calls, [quantile(curve[(c, dt)][i], .5) for i in calls], "o-", color=colors.get(c), label=NAME[c])
    if ("hot", 0) in curve:
        ax2.plot(calls, [quantile(curve[("hot", 0)][i], .5) for i in calls], "k:", label="hot")
    ax2.set_xlabel("call number after the pause")
    ax2.set_ylabel("p50, µs")
    ax2.set_title(f"Warm-up curve, Δt = {dt} ms")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=.3)

    fig.tight_layout()
    png = os.path.join(os.path.dirname(os.path.abspath(path)), "plot.png")
    fig.savefig(png, dpi=120)
    print(f"Plot: {png}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    text, first, curve = report(sys.argv[1])
    print(text)
    plot(sys.argv[1], first, curve)
    if len(sys.argv) > 2:
        first_b, _, _ = load(sys.argv[2])
        print()
        print(compare(first, first_b))


if __name__ == "__main__":
    main()
