#!/usr/bin/env python3
"""Simple figures for docs/shadow.md and docs/prefetch.md: one message each, no legend boxes.

Every piece of on-canvas text (titles, axis labels, series labels, notes) comes from docs/figures.ru.json;
this file holds no Russian text. Inputs and run selection are those of figures.py and iso.py:

  period.png          penalty vs warm-up period T in the spin loop                    (figures.fig04 runs)
  size.png            share of the spin penalty removed by shadow execution / prefetch (figures.fig13 runs)
  online_system.png   live stream, architecture A, other pairs: p50 / p99 / p999      (tails-L1.txt; fig08 CCDF)
  tick.png            penalty vs time since the last interrupt, Δt = 10 ms             (figures.fig20 runs)
  l3_neighbour.png    penalty with a neighbour sharing L3 on another core              (iso.py cells)
  counters_c6.png     counters after C6 left by prefetch and prewake, % of sleep       (figures.fig06 runs)
  prefetch_sleep.png  penalty after C3 / C6: sleep, pfwake, prewake                    (figures.fig05 runs)

Penalty = p50(first call after the pause) − p50(hot call of the same run), ns. The values each figure
draws are printed, to be checked against the numbers in the documents.

Usage (from cold-path): python3 docs_figures.py [--only period,size] [--out DIR]
"""
import sys

sys.dont_write_bytecode = True  # importing figures / iso / size must not leave .pyc files in the project

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from collections import defaultdict  # noqa: E402

import figures as F  # noqa: E402  (imports numpy and matplotlib with the Agg backend)
import numpy as np  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import ticker  # noqa: E402

HERE = F.HERE
TEXT = os.path.join(HERE, "docs", "figures.ru.json")
OUT = os.path.join(HERE, "docs", "img")
TAILS = os.path.join(F.ROOT, "online", "tails-L1.txt")
W, DPI = 9.6, 200  # inches, dots per inch: ~1900 px wide, readable when GitHub scales it to the page
DT = 10            # pause Δt, ms, in every figure
INK, INK2, GRID, AXIS, REF = F.INK, F.INK2, F.GRID, F.AXIS, F.MUTED


# ---------------------------------------------------------------- style and text
def setup_style():
    plt.rcParams.update({
        "font.family": ["PT Sans", "Arial", "DejaVu Sans"], "font.size": 12,
        "figure.facecolor": F.SURF, "axes.facecolor": F.SURF, "savefig.facecolor": F.SURF,
        "axes.edgecolor": AXIS, "axes.linewidth": 1.0, "axes.labelcolor": INK2, "axes.labelsize": 12.5,
        "axes.labelpad": 8, "axes.spines.top": False, "axes.spines.right": False,
        "axes.titlesize": 14, "axes.titleweight": "bold", "axes.titlecolor": INK, "axes.titlelocation": "left",
        "axes.titlepad": 12,
        "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 12, "ytick.labelsize": 12,
        "xtick.major.size": 0, "ytick.major.size": 0, "xtick.minor.size": 0, "ytick.minor.size": 0,
        "xtick.major.pad": 7, "ytick.major.pad": 7,
        "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
    })


def fnum(v, nd=0):
    """Number in Russian notation: decimal comma, typographic minus, no '−0'."""
    s = f"{v:.{nd}f}"
    if float(s) == 0:
        s = s.lstrip("-")
    return s.replace(".", ",").replace("-", "−")


def sig(v, n=2):
    """Number of decimals that keeps n significant digits (never negative)."""
    return 0 if v == 0 else max(0, n - 1 - math.floor(math.log10(abs(v))))


def comma_axis(ax, which="y", suffix=""):
    def f(v, _pos):
        nd = next(d for d in range(4) if abs(v * 10 ** d - round(v * 10 ** d)) < 1e-6)
        return fnum(v, nd) + suffix
    (ax.yaxis if which == "y" else ax.xaxis).set_major_formatter(ticker.FuncFormatter(f))


def canvas(t, h, ncols=1, *, left=1.0, right=0.3, bottom=0.8, panel_titles=False, wspace=0.12, sharey=True,
           grid="y"):
    """Figure W × h inches: title (the takeaway) and one subtitle line, then 1–3 panels in a row."""
    fig = plt.figure(figsize=(W, h), dpi=DPI)
    fig.text(0.22 / W, 1 - 0.22 / h, t["title"], ha="left", va="top", fontsize=17.5, weight="bold", color=INK)
    fig.text(0.22 / W, 1 - 0.68 / h, t["subtitle"], ha="left", va="top", fontsize=12.5, color=INK2)
    top = 1.05 + (0.45 if panel_titles else 0)
    gs = fig.add_gridspec(1, ncols, left=left / W, right=1 - right / W, bottom=bottom / h, top=1 - top / h,
                          wspace=wspace)
    axes = []
    for i in range(ncols):
        ax = fig.add_subplot(gs[i], sharey=axes[0] if axes and sharey else None)
        ax.set_axisbelow(True)
        if grid:
            ax.grid(axis=grid, color=GRID, linewidth=0.9)
        ax.spines["left"].set_visible(False)
        if i and sharey:
            ax.tick_params(labelleft=False)
        axes.append(ax)
    return fig, axes


def zero_line(ax):
    """Baseline at 0 instead of the bottom spine."""
    ax.spines["bottom"].set_visible(False)
    ax.axhline(0, color=AXIS, linewidth=1.2, zorder=1)


def bars(ax, xs, vals, colors, labels, *, fmt=lambda v: fnum(v), width=0.62, size=13):
    ax.bar(xs, vals, width=width, color=colors, linewidth=0, zorder=2)
    for x, v in zip(xs, vals):
        ax.annotate(fmt(v), (x, max(v, 0)), xytext=(0, 5), textcoords="offset points", ha="center", va="bottom",
                    fontsize=size, color=INK)
    ax.set_xticks(xs, labels)
    ax.set_xlim(min(xs) - 0.6, max(xs) + 0.6)


def save(fig, name, out):
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    print(f"  -> {os.path.relpath(path, HERE)}")


def skip(name, why):
    print(f"  {name}: skipped, {why}")


def show(label, values):
    print(f"  {label}: " + ", ".join(f"{k} {v}" for k, v in values))


# ---------------------------------------------------------------- 1. warm-up period T in the spin loop
def period_runs():
    """T, µs → runs, as in figures.fig04 (POLL): f0-ss<T>, pC0-T<T>; pC0-r* at T = 50 µs; s0-r* at T = 1 ms."""
    by_t = defaultdict(list)
    for pat, rx in (("f0-ss*", r"f0-ss(\d+)"), ("pC0-T*", r"pC0-T(\d+)")):
        for r in F.runs(pat):
            m = re.fullmatch(rx, r.name)
            if m:
                by_t[int(m.group(1))].append(r)
    by_t[50] += F.runs("pC0-r*")
    by_t[1000] += F.runs("s0-r*")
    return {k: v for k, v in sorted(by_t.items()) if v}


LABEL_T = (10, 50, 100, 1000)  # periods whose shadow-execution penalty the documents quote


def fig_period(t, out):
    name = "period.png"
    by_t = period_runs()
    if len(by_t) < 3:
        return skip(name, "no period sweep (runs *-f0-ss*, *-pC0-T*)")
    lines = {}
    for m in ("spin", "spinpf", "spinshadow"):
        pts = []
        for T, rr in by_t.items():
            rr = [r for r in rr if r.has(m, DT)]
            if rr:  # all trials of the cell pooled, as in fig04
                pts.append((T, F.q(np.concatenate([r.excess(m, DT) for r in rr]), .5)))
        if pts:
            lines[m] = pts
        show(f"period {m}", [(f"T={T}", fnum(v)) for T, v in pts])
    ts = list(by_t)
    fig, (ax,) = canvas(t, 5.4, left=0.95, right=0.3, bottom=0.85)
    x0, x1 = ts[0] / 1.35, ts[-1] * 1.35
    ax.axvspan(74, x1, color=GRID, alpha=.55, linewidth=0, zorder=0)
    ax.text(80, 172, t["zone"], ha="left", va="top", fontsize=12.5, color=INK2)
    for m, pts in lines.items():
        x, y = zip(*pts)
        ax.plot(x, y, color=F.col(m), linewidth=2.6, marker="o", markersize=8.5, markeredgecolor=F.SURF,
                markeredgewidth=1.5, zorder=3, clip_on=False)
    # direct labels at the left end, above each line, where the three lines are far apart
    for m, dy in (("spin", 9), ("spinpf", 9), ("spinshadow", 40)):
        if m in lines:
            T, v = lines[m][0]
            ax.annotate(t["series"][m], (T, v), xytext=(-4, dy), textcoords="offset points", ha="left",
                        va="bottom", fontsize=12.5, color=INK, weight="bold")
    # values of shadow execution at the periods the text quotes: above the marker, the last one below
    for T, v in lines.get("spinshadow", []):
        if T in LABEL_T:
            last = T == ts[-1]
            ax.annotate(fnum(v, 1 if v < 9.5 else 0), (T, v), xytext=(0, -12 if last else 11),
                        textcoords="offset points", ha="center", va="top" if last else "bottom",
                        fontsize=12, color=INK)
    ax.set_xscale("log")
    ax.set_xticks(ts, [fnum(T) for T in ts])
    ax.xaxis.set_minor_locator(ticker.NullLocator())
    ax.set_xlim(x0, x1)
    ax.set_ylim(-4, 175)
    ax.set_yticks([0, 50, 100, 150])
    comma_axis(ax)
    zero_line(ax)
    ax.set_xlabel(t["xlabel"])
    ax.set_ylabel(t["ylabel"])
    save(fig, name, out)


# ---------------------------------------------------------------- 2. handler size: share removed and cost
SIZE_POINTS = ["cpptrader", "tx", "c32", "c128"]


def fig_size(t, out):
    name = "size.png"
    S = F.size_api()
    pts = [p for p in SIZE_POINTS if S and S.runs(rf"szB-{p}-r\d+")]
    if not pts:
        return skip(name, "no runs *-szB-<point>-r* or no size.py")
    fig, axes = canvas(t, 5.15, 2, left=0.95, right=0.2, bottom=1.0, panel_titles=True, wspace=0.08)
    for ax, m in zip(axes, ("spinshadow", "spinpf")):
        xs, rem, cost = [], [], []
        for i, p in enumerate(pts):
            rx = rf"szB-{p}-r\d+"
            r_ = F.sz(rx, lambda r, m=m: r.removed("spin", m, .5))
            c_ = F.sz(rx, lambda r, m=m: r.cost(m))
            # as in fig13: the share is shown only if the spin penalty is above 50 ns in every run
            if not r_ or not c_ or r_[3] != len(S.runs(rx)):
                print(f"  size {m} {p}: no share (spin penalty ≤ 50 ns in some run)")
                continue
            xs.append(i), rem.append(r_[0]), cost.append(c_[0])
        show(f"size {m} removed %", [(p, fnum(v)) for p, v in zip(pts, rem)])
        show(f"size {m} cost %", [(p, fnum(v, sig(v))) for p, v in zip(pts, cost)])
        ax.bar(xs, rem, width=0.62, color=F.col(m), linewidth=0, zorder=2)
        for x, v, c in zip(xs, rem, cost):
            ax.annotate(t["removed"].format(v=fnum(v)), (x, v), xytext=(0, 22), textcoords="offset points",
                        ha="center", va="bottom", fontsize=14, weight="bold", color=INK)
            ax.annotate(t["cost"].format(v=fnum(c, sig(c))), (x, v), xytext=(0, 5), textcoords="offset points",
                        ha="center", va="bottom", fontsize=11.5, color=INK2)
        ax.set_xticks(range(len(pts)), [t["points"][p] for p in pts])
        ax.set_xlim(-0.6, len(pts) - 0.4)
        ax.set_title(t["panels"][m])
        zero_line(ax)
    axes[0].set_ylim(0, 128)
    axes[0].set_yticks([0, 25, 50, 75, 100])
    comma_axis(axes[0])
    axes[0].set_ylabel(t["ylabel"])
    save(fig, name, out)


# ---------------------------------------------------------------- 3. live stream: system tail
ONLINE_MODES = ["spin", "spinshadow", "sleep"]


def tails_system(path):
    """mode → (p50, p99, p999), µs, from the system table of tails.py output (rows: mode, packets, p50, p99,
    p999, max, '|'); None if the file is missing."""
    if not os.path.exists(path):
        return None
    rx = re.compile(r"^\s*(\w+)\s+\d+\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+[\d.]+\s+\|", re.M)
    with open(path, encoding="utf-8") as f:
        return {m.group(1): tuple(float(m.group(k)) for k in (2, 3, 4)) for m in rx.finditer(f.read())}


def ccdf_system():
    """mode → (p50, p99, p999), µs, read off the export.csv system CCDF (fig08): nearest point in −log10 share."""
    ex = F.read_export(F.ONLINE)
    if not ex:
        return None
    S = F.pick_series(ex, ["A"], None, "system_ccdf")
    if not S:
        return None
    out = {}
    for m in ONLINE_MODES:
        v = ex.get((S["A"], "A", m, "system_ccdf"))
        if v:
            out[m] = tuple(F.at(v, e)[1] for e in (math.log10(2), 2, 3))
    return out


def fig_online(t, out):
    name = "online_system.png"
    tails, ccdf = tails_system(TAILS), ccdf_system()
    for src, d in (("tails-L1.txt", tails), ("export.csv CCDF", ccdf)):
        for m in ONLINE_MODES:
            if d and m in d:
                show(f"online {src} {m} p50/p99/p999 us", [("", "/".join(fnum(x, 2) for x in d[m]))])
    vals = tails if tails and all(m in tails for m in ONLINE_MODES) else ccdf
    if not vals or not all(m in vals for m in ONLINE_MODES):
        return skip(name, f"no system quantiles in {TAILS} or {F.ONLINE}")
    fig, (ax,) = canvas(t, 4.6, left=1.85, right=0.35, bottom=0.85, grid=None)
    for i, m in enumerate(ONLINE_MODES):
        v = vals[m]
        c = F.col(m)
        ax.plot(v, [i] * 3, color=c, linewidth=2.6, zorder=2)
        ax.plot(v, [i] * 3, "o", markersize=12, color=c, markeredgecolor=F.SURF, markeredgewidth=1.6, zorder=3)
        for x in v:
            ax.annotate(fnum(x, sig(x, 3)), (x, i), xytext=(0, -15), textcoords="offset points", ha="center",
                        va="top", fontsize=12, color=INK)
    for x, q in zip(vals[ONLINE_MODES[0]], t["quantiles"]):
        ax.annotate(q, (x, 0), xytext=(0, 13), textcoords="offset points", ha="center", va="bottom",
                    fontsize=12, color=INK2, weight="bold")
    ax.set_xscale("log")
    ax.set_xlim(3.4, 85)
    ax.set_xticks([5, 10, 20, 50], [fnum(x) for x in (5, 10, 20, 50)])
    ax.xaxis.set_minor_locator(ticker.NullLocator())
    ax.set_ylim(len(ONLINE_MODES) - 0.45, -0.6)
    ax.set_yticks(range(len(ONLINE_MODES)), [t["modes"][m] for m in ONLINE_MODES])
    for lab in ax.get_yticklabels():
        lab.set_color(INK)
        lab.set_fontsize(13)
    ax.set_xlabel(t["xlabel"])
    save(fig, name, out)


# ---------------------------------------------------------------- 4. tick: time since the last interrupt
def fig_tick(t, out):
    name = "tick.png"
    rr = F.runs("nztB-10") + F.runs("nztT-10")  # with and without the tick, pooled (fig20, left panel)
    if not rr:
        return skip(name, "no runs *-nztB-10, *-nztT-10")
    edges = np.array([0, 5, 10, 20, 50, 100, 200, 300, 500, 700, 1000])
    mids = np.sqrt(np.maximum(edges[:-1], 2) * edges[1:])
    lines, clean = {}, {}
    for m in ("spin", "spinshadow"):
        have = [r for r in rr if r.has(m, DT)]
        if not have:
            continue
        ex = np.concatenate([r.excess(m, DT) for r in have])
        gap = np.concatenate([r.cells[(m, DT)]["gap_us"] for r in have])
        pts = []
        for k in range(len(edges) - 1):
            v = ex[(gap >= edges[k]) & (gap < edges[k + 1])]
            if len(v) >= 100:
                pts.append((mids[k], F.q(v, .5)))
        lines[m] = pts
        if (gap < 0).sum() >= 100:
            clean[m] = F.q(ex[gap < 0], .5)
        show(f"tick {m} p50 by bin", [(f"{lo}-{hi}us", fnum(v)) for (lo, hi), (_, v)
                                       in zip(zip(edges[:-1], edges[1:]), pts)])
        show(f"tick {m}", [("no interrupt", fnum(clean.get(m, float("nan")))),
                           ("<50us pooled", fnum(F.q(ex[(gap >= 0) & (gap < 50)], .5))),
                           ("100us-1ms pooled", fnum(F.q(ex[(gap >= 100) & (gap < 1000)], .5)))])
    fig, (ax,) = canvas(t, 5.2, left=0.95, right=0.3, bottom=0.85)
    if "spin" in clean:
        c50 = clean["spin"]
        ax.axhline(c50, color=REF, linestyle=(0, (1.5, 2.5)), linewidth=2, zorder=1)
        ax.annotate(t["clean"].format(v=fnum(c50)), (2.6, c50), xytext=(0, 6), textcoords="offset points",
                    ha="left", va="bottom", fontsize=12, color=INK2)
    for m, pts in lines.items():
        x, y = zip(*pts)
        ax.plot(x, y, color=F.col(m), linewidth=2.6, marker="o", markersize=8.5, markeredgecolor=F.SURF,
                markeredgewidth=1.5, zorder=3, clip_on=False)
        xl, yl = pts[-3]  # label above the flat right part of the line, the value at its end
        ax.annotate(t["series"][m], (xl, yl), xytext=(0, 12), textcoords="offset points", ha="center",
                    va="bottom", fontsize=12.5, color=INK, weight="bold")
        ax.annotate(fnum(y[-1], 1 if y[-1] < 9.5 else 0), (x[-1], y[-1]), xytext=(10, 0),
                    textcoords="offset points", ha="left", va="center", fontsize=12, color=INK)
    ax.set_xscale("log")
    xt = [5, 10, 20, 50, 100, 200, 500, 1000]
    ax.set_xticks(xt, [fnum(x) for x in xt])
    ax.xaxis.set_minor_locator(ticker.NullLocator())
    ax.set_xlim(2.4, 1250)
    ax.set_ylim(-4, 128)
    ax.set_yticks([0, 40, 80, 120])
    comma_axis(ax)
    zero_line(ax)
    ax.set_xlabel(t["xlabel"])
    ax.set_ylabel(t["ylabel"])
    save(fig, name, out)


# ---------------------------------------------------------------- 5. neighbour sharing L3
# iso.py cells (results/iso-summary.txt): no neighbour = [i3] ded, neighbour no; the others = [i5] l3; POLL, spin kind
ISO_CELLS = [("no", "*-i3-ded-no-p0-spin-r*"), ("mem64m", "*-i5-l3-mem64m-p0-spin-r*"),
             ("mix", "*-i5-l3-mix-p0-spin-r*")]
LOOP_MODES = ["spin", "spinpf", "spinshadow"]


def fig_l3(t, out):
    name = "l3_neighbour.png"
    import iso  # the summary script of the iso series: its load_run (penalty to hot p50 of the run) and quantile
    vals = {}
    for cell, pat in ISO_CELLS:
        dirs = sorted(d for d in glob.glob(os.path.join(F.ROOT, pat)) if os.path.exists(os.path.join(d, "calls.csv")))
        pooled = defaultdict(list)  # the runs of a cell are pooled, as in iso.py
        for d in dirs:
            data = iso.load_run(d)
            for m in LOOP_MODES:
                pooled[m].extend(data.get((m, DT), {}).get("ex", []))
        if all(pooled[m] for m in LOOP_MODES):
            vals[cell] = [iso.quantile(pooled[m], .5) for m in LOOP_MODES]
            show(f"l3 {cell} ({len(dirs)} runs)", [(m, fnum(v)) for m, v in zip(LOOP_MODES, vals[cell])])
    if len(vals) < len(ISO_CELLS):
        return skip(name, "missing iso cells: " + ", ".join(c for c, _ in ISO_CELLS if c not in vals))
    fig, axes = canvas(t, 4.9, 3, left=0.95, right=0.2, bottom=0.85, panel_titles=True, wspace=0.1)
    for ax, (cell, _) in zip(axes, ISO_CELLS):
        bars(ax, range(3), vals[cell], [F.col(m) for m in LOOP_MODES], [t["modes"][m] for m in LOOP_MODES])
        ax.set_title(t["panels"][cell])
        zero_line(ax)
    axes[0].set_ylim(0, 1950)
    axes[0].set_yticks([0, 500, 1000, 1500])
    comma_axis(axes[0])
    axes[0].set_ylabel(t["ylabel"])
    save(fig, name, out)


# ---------------------------------------------------------------- 6. counters after C6
COUNTER_ROWS = [("cache", ["l1d_miss", "code_l2_miss", "dtlb_walk", "itlb_walk"]),
                ("frontend", ["ic_miss", "br_miss", "baclears"])]
# (mode, prefetch set) of the counter runs *-pB8-<set>-<group>, as in fig06; pfwake = prefetch code + data
COUNTER_CELLS = [("none", "0"), ("pfwake", "dc"), ("prewake", "0")]


def counter_means():
    """(counter, mode) → first-call mean over the run, whichever group a..d holds the counter (fig06)."""
    found = {}
    for _, cnts in COUNTER_ROWS:
        for cnt in cnts:
            for m, s in COUNTER_CELLS:
                for g in "abcd":
                    for r in F.runs(f"pB8-{s}-{g}"):
                        if r.has(m, DT) and cnt in r.cells[(m, DT)]:
                            found[(cnt, m)] = float(np.mean(r.cells[(m, DT)][cnt]))
    return found


def fig_counters(t, out):
    name = "counters_c6.png"
    found = counter_means()
    rows, y, heads = [], 0.0, []
    for grp, cnts in COUNTER_ROWS:
        heads.append((y, t["groups"][grp]))
        y += 1
        for cnt in cnts:
            if all((cnt, m) in found for m, _ in COUNTER_CELLS):
                rows.append((y, cnt))
                y += 1
        y += 0.35
    if not rows:
        return skip(name, "no counter runs *-pB8-{0,dc}-{a,b,c,d}")
    for _, cnt in rows:
        show(f"counters {cnt} sleep/prefetch/prewake", [("", "/".join(fnum(found[(cnt, m)], sig(found[(cnt, m)])) for
                                                                  m, _ in COUNTER_CELLS))])
    fig, axes = canvas(t, 6.0, 2, left=2.55, right=0.3, bottom=0.55, panel_titles=True, wspace=0.12, grid=None)
    for ax, m in zip(axes, ("pfwake", "prewake")):
        share = [100 * found[(cnt, m)] / found[(cnt, "none")] for _, cnt in rows]
        show(f"counters {m} % of sleep", [(cnt, fnum(v)) for (_, cnt), v in zip(rows, share)])
        ys = [r[0] for r in rows]
        ax.barh(ys, share, height=0.64, color=F.col(m), linewidth=0, zorder=2)
        for yy, v in zip(ys, share):
            ax.annotate(t["value"].format(v=fnum(v)), (v, yy), xytext=(6, 0), textcoords="offset points",
                        ha="left", va="center", fontsize=12, color=INK, zorder=4,
                        bbox=dict(boxstyle="square,pad=0.12", facecolor=F.SURF, edgecolor="none"))
        ax.axvline(100, color=REF, linestyle=(0, (4, 3)), linewidth=1.4, zorder=1)
        ax.annotate(t["ref"], (100, heads[0][0]), ha="center", va="center", fontsize=11.5, color=INK2,
                    bbox=dict(boxstyle="square,pad=0.15", facecolor=F.SURF, edgecolor="none"))
        ax.axvline(0, color=AXIS, linewidth=1.2, zorder=1)
        ax.set_xlim(0, 128)
        ax.set_xticks([])
        ax.spines["bottom"].set_visible(False)
        ax.set_title(t["panels"][m])
    ax0 = axes[0]
    ax0.set_ylim(y - 0.35 - 0.5, heads[0][0] - 0.6)
    ticks = [h for h, _ in heads] + [r[0] for r in rows]
    labels = [lab for _, lab in heads] + [t["rows"][cnt] for _, cnt in rows]
    ax0.set_yticks(ticks, labels)
    for lab, tk in zip(ax0.get_yticklabels(), ticks):
        head = tk in [h for h, _ in heads]
        lab.set_color(INK2 if head else INK)
        lab.set_fontweight("bold" if head else "normal")
        lab.set_fontsize(12 if head else 12.5)
    save(fig, name, out)


# ---------------------------------------------------------------- 7. prefetch after sleep
SLEEP_MODES = [("none", None), ("pfwake", "dc"), ("prewake", None)]


def fig_prefetch_sleep(t, out):
    name = "prefetch_sleep.png"
    vals = {}
    for pol in ("3", "8"):
        by_set = {s: F.runs(f"pA{pol}-{s}-r*") for s in ("0", "d", "c", "dc")}
        allr = [r for v in by_set.values() for r in v]
        v = []
        for m, s in SLEEP_MODES:  # median over runs of the per-run p50 penalty, as in fig05
            rr = [r for r in (allr if s is None else by_set[s]) if r.has(m, DT)]
            v.append(F.med([r.pen(m, DT) for r in rr]) if rr else float("nan"))
        if all(x == x for x in v):
            vals[pol] = v
            show(f"prefetch_sleep C{'6' if pol == '8' else pol}", [(m, fnum(x)) for (m, _), x in zip(SLEEP_MODES, v)])
    if len(vals) < 2:
        return skip(name, "no runs *-pA{3,8}-{0,d,c,dc}-r*")
    fig, axes = canvas(t, 4.85, 2, left=0.95, right=0.2, bottom=0.85, panel_titles=True, wspace=0.1)
    for ax, pol in zip(axes, ("3", "8")):
        bars(ax, range(3), vals[pol], [F.col(m) for m, _ in SLEEP_MODES], [t["modes"][m] for m, _ in SLEEP_MODES])
        ax.set_title(t["panels"][pol])
        zero_line(ax)
    axes[0].set_ylim(0, 1080)
    axes[0].set_yticks([0, 250, 500, 750, 1000])
    comma_axis(axes[0])
    axes[0].set_ylabel(t["ylabel"])
    save(fig, name, out)


# ---------------------------------------------------------------- main
FIGS = [("period", fig_period), ("size", fig_size), ("online_system", fig_online), ("tick", fig_tick),
        ("l3_neighbour", fig_l3), ("counters_c6", fig_counters), ("prefetch_sleep", fig_prefetch_sleep)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated figure names, e.g. period,tick")
    ap.add_argument("--out", default=OUT, help="where to save the PNGs (default docs/img)")
    a = ap.parse_args()
    with open(TEXT, encoding="utf-8") as f:
        text = json.load(f)
    setup_style()
    only = set(a.only.split(",")) if a.only else None
    for key, fn in FIGS:
        if only and key not in only:
            continue
        print(f"{key}:")
        fn(text[key], a.out)


if __name__ == "__main__":
    main()
