#!/usr/bin/env python3
"""Publication figures for the cold-path study (English labels), one function per figure.

Every figure function finds its own inputs; if they are missing it prints why and is skipped.
PNG, 150 dpi, into ../figures/ (next to cold-path/).

  fig01  "How we wait" (Intel): event → end of the first call and penalty, by wait mode and idle policy
  fig02  cooling curve (Intel): first call vs pause Δt, by idle policy
  fig03  warm-up curve: calls 1…8 after a pause vs hot
  fig04  shadow and prefetch in the spin loop: remaining penalty vs period T
  fig05  prefetch after C3 and C6: penalty split into data, code in L2 and the frontend remainder
  fig06  counters after C6: L1i, BTB, mispredictions, DSB
  fig07a AMD Zen 5 vs Intel in the same cells; fig07b AMD: busy CCD neighbour
  fig08–fig11 online stand, from results/online/export.csv

Usage:
  python3 figures.py [--only 1,5,7] [--results DIR] [--amd DIR] [--online FILE] [--out DIR]

Run-level conventions are those of analyze.py / series.py / pf.py: the first DISCARD trials of a run
are dropped; penalty = p50(mode) − p50(hot) of the same run; quantile = sorted[int(p·n)].
AMD runs: `cycles` is exact core cycles, `ns` is TSC in ~10 ns steps; time = cycles / f with
f = Σcycles / Σns over the run's hot rows.

Colour = wait mode, the same in every figure (validated with the dataviz skill's
validate_palette.js on every set of modes that share a figure; marker shape is the second channel).
"""
import argparse
import csv
import glob
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
# Import the plotting stack with this directory off sys.path, so that a local script named like a
# standard-library module (there was a dis.py here once) cannot shadow it; then put the directory back.
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != HERE]
import numpy as np  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import ticker  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

sys.path.append(HERE)
try:
    from analyze import DISCARD  # noqa: E402
except Exception:  # analyze.py unavailable or broken: keep its documented value
    DISCARD = 50

ROOT = os.path.join(HERE, "results")
AMD = "/tmp/amd-nb"
ONLINE = os.path.join(ROOT, "online", "export.csv")
OUT = os.path.join(os.path.dirname(HERE), "figures")
DPI = 150

# ---------------------------------------------------------------- style (dataviz reference palette)
SURF, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"

# One colour + marker per wait mode in every figure. Slots of the reference categorical palette,
# chosen so that every pair of modes that ever share a figure clears the normal-vision floor
# (validate_palette.js --pairs all: worst pair aqua/green ΔE 15.6; CVD warns ≥ 6.1 are covered by
# the marker shape + legend/direct labels). Eight hues for eight modes would put orange on pfevent,
# which shares fig18 with sleep (yellow) and prewake (red) — both below the normal-vision floor with
# orange — so pfevent is a dark neutral and the empty-wake control a light one; orange stays unused.
# Online names: sleep = none, sleepshadow = shadow.
MODES = {
    "hot":        ("#898781", "o", "hot (no pause)"),
    "spin":       ("#2a78d6", "o", "busy-wait loop (spin)"),
    "spinshadow": ("#1baf7a", "s", "spin + shadow"),
    "spinpf":     ("#e87ba4", "X", "spin + prefetch"),
    "none":       ("#eda100", "v", "sleep, no warm-up"),
    "shadow":     ("#008300", "^", "sleep + shadow"),
    "empty":      ("#b0aea7", "h", "empty wake-ups (control)"),
    "prewake":    ("#e34948", "D", "prewake: wake early and warm up"),
    "pfwake":     ("#6250d6", "P", "pfwake: wake early and prefetch"),
    "pfevent":    ("#52514e", "*", "pfevent: prefetch at the event"),
}
ALIAS = {"sleep": "none", "sleepshadow": "shadow"}
ONLINE_NAME = {"sleep": "sleep", "spin": "busy-wait (spin)", "spinshadow": "spin + shadow",
               "sleepshadow": "sleep + shadow", "spinpf": "spin + prefetch"}
ONLINE_ORDER = ["sleep", "sleepshadow", "spin", "spinpf", "spinshadow"]


def col(m):
    return MODES[ALIAS.get(m, m)][0]


def mk(m):
    return MODES[ALIAS.get(m, m)][1]


def setup_style():
    plt.rcParams.update({
        "font.family": ["Arial", "DejaVu Sans"], "font.size": 10,
        "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK2, "axes.labelsize": 10,
        "axes.titlesize": 10.5, "axes.titlecolor": INK, "axes.titleweight": "bold",
        "axes.titlelocation": "left", "axes.titlepad": 8,
        "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "xtick.major.size": 3, "ytick.major.size": 3, "xtick.minor.size": 0, "ytick.minor.size": 0,
        "xtick.major.width": 0.8, "ytick.major.width": 0.8,
        "lines.linewidth": 2, "lines.markersize": 7, "lines.solid_capstyle": "round",
        "lines.solid_joinstyle": "round", "legend.frameon": False, "legend.fontsize": 9,
        "axes.spines.top": False, "axes.spines.right": False,
    })


def style(ax, grid="y"):
    ax.set_axisbelow(True)
    if grid:
        ax.grid(axis=grid, color=GRID, linewidth=0.8, linestyle="-")
    ax.tick_params(which="both", color=AXIS)


def num(v, nd=0):
    """Number with a decimal point and a typographic minus."""
    s = f"{v:.{nd}f}"
    return s.replace("-", "−")


def plain_fmt(v, _pos=None):
    if v <= 0:
        return ""
    s = f"{v:.3g}"
    if "e" in s:
        s = f"{v:.0f}"
    return s


def log_axis(ax, which="x"):
    a = ax.xaxis if which == "x" else ax.yaxis
    (ax.set_xscale if which == "x" else ax.set_yscale)("log")
    a.set_major_formatter(ticker.FuncFormatter(plain_fmt))
    a.set_minor_formatter(ticker.NullFormatter())


def nice_log_ticks(ax, which="y", n=6):
    """Readable ticks on a log axis spanning less than ~2 decades: 1, 1.2, 1.5, 2, 3, 5, 7 × 10^k."""
    lo, hi = ax.get_ylim() if which == "y" else ax.get_xlim()
    if lo <= 0:
        return
    cand = [m * 10 ** k for k in range(int(np.floor(np.log10(lo))) - 1, int(np.ceil(np.log10(hi))) + 1)
            for m in ((1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2, 2.5, 3, 4, 5, 6, 7, 8) if hi / lo < 4
                      else (1, 1.2, 1.5, 2, 3, 5, 7))]
    cand = [c for c in cand if lo <= c <= hi]
    if len(cand) > n:
        idx = np.unique(np.round(np.linspace(0, len(cand) - 1, n)).astype(int))
        cand = [cand[i] for i in idx]
    (ax.yaxis if which == "y" else ax.xaxis).set_major_locator(ticker.FixedLocator(cand))


def frame(w, h, title, subtitle, *, left=0.75, right=0.25, bottom=0.65, legend_rows=1, panel_titles=True):
    """Figure with a left-aligned title, subtitle and a reserved legend band; returns (fig, rect)."""
    fig = plt.figure(figsize=(w, h), dpi=DPI)
    n_sub = subtitle.count("\n") + 1 if subtitle else 0
    fig.text(0.18 / w, 1 - 0.16 / h, title, ha="left", va="top", fontsize=13.5, weight="bold", color=INK)
    if subtitle:
        fig.text(0.18 / w, 1 - 0.50 / h, subtitle, ha="left", va="top", fontsize=9.5, color=INK2,
                 linespacing=1.4)
    head = 0.55 + 0.22 * n_sub + 0.30 * legend_rows + (0.55 if panel_titles else 0.25)
    fig._legend_y = 1 - (0.55 + 0.22 * n_sub + 0.12) / h  # top of the legend band
    rect = dict(left=left / w, right=1 - right / w, bottom=bottom / h, top=1 - head / h)
    return fig, rect


def legend(fig, handles, ncol=None, x=None):
    if not handles:
        return
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(x if x is not None else 0.14 / fig.get_figwidth(),
                                                                 fig._legend_y),
               ncol=ncol or len(handles), handlelength=2.2, columnspacing=1.6, handletextpad=0.6,
               labelcolor=INK, borderaxespad=0)


def key(m, label=None, line=True, **kw):
    """Legend handle: coloured line + marker, label in ink."""
    d = dict(color=col(m), marker=mk(m), markersize=8, markeredgecolor=SURF, markeredgewidth=1.2,
             linewidth=2 if line else 0, label=label or MODES[ALIAS.get(m, m)][2])
    d.update(kw)
    return Line2D([], [], **d)


def save(fig, name, out):
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    print(f"  → {path}")
    return path


def skip(name, why):
    print(f"{name}: skipped — {why}")


# ---------------------------------------------------------------- data
def q(x, p):
    """Quantile as in analyze.py: sorted[min(n − 1, int(p·n))]."""
    s = np.sort(np.asarray(x, float))
    return float(s[min(len(s) - 1, int(p * len(s)))]) if len(s) else float("nan")


_CACHE = {}


class Run:
    """One calls.csv. cells[(cond, Δt)] = {'calls': {k: ns array}, 'ns': first-call ns, field: first-call array}."""

    def __init__(self, path, cycles_time=False):
        self.path = path
        self.name = re.sub(r"^\d+-\d+-", "", os.path.basename(os.path.dirname(path)))
        rows = defaultdict(lambda: defaultdict(list))
        with open(path) as f:
            rd = csv.reader(f)
            hdr = next(rd)
            ix = {k: i for i, k in enumerate(hdr)}
            extra = [k for k in hdr if k not in ("trial", "cond", "dt_ms", "call")]
            for r in rd:
                if len(r) != len(hdr) or int(r[ix["trial"]]) < DISCARD:
                    continue
                c = rows[(r[ix["cond"]], int(r[ix["dt_ms"]]))]
                c["call"].append(int(r[ix["call"]]))
                for k in extra:
                    c[k].append(float(r[ix[k]]))
        self.ghz = None
        if cycles_time and "cycles" in hdr and ("hot", 0) in rows:
            h = rows[("hot", 0)]
            self.ghz = sum(h["cycles"]) / sum(h["ns"])
        self.cells = {}
        for key_, c in rows.items():
            call = np.array(c["call"])
            ns = np.array(c["cycles"]) / self.ghz if self.ghz else np.array(c["ns"])
            first = call == 1
            cell = {"calls": {k: ns[call == k] for k in range(1, 9) if (call == k).any()}, "ns": ns[first]}
            for k in c:
                if k not in ("call", "ns"):
                    cell[k] = np.array(c[k])[first]
            self.cells[key_] = cell
        self.hot = q(self.cells[("hot", 0)]["ns"], .5) if ("hot", 0) in self.cells else float("nan")

    def has(self, m, dt):
        return (m, dt) in self.cells

    def ns(self, m, dt):
        return self.cells[(m, dt)]["ns"]

    def pen(self, m, dt):
        """p50 penalty to hot of the same run, ns."""
        return q(self.ns(m, dt), .5) - self.hot

    def excess(self, m, dt):
        """Per-trial first call minus the run's hot p50, ns."""
        return self.ns(m, dt) - self.hot

    def e2e(self, m, dt):
        """Per-trial event → end of first call, µs."""
        c = self.cells[(m, dt)]
        return c["late_us"] + c["ns"] / 1000


def runs(pattern, root=None, cycles_time=False):
    """Complete runs */<stamp>-<pattern>/calls.csv (those with a hot cell), sorted by time."""
    out = []
    for p in sorted(glob.glob(os.path.join(root or ROOT, f"*-{pattern}", "calls.csv"))):
        k = (p, cycles_time)
        if k not in _CACHE:
            try:
                _CACHE[k] = Run(p, cycles_time)
            except (OSError, ValueError, StopIteration, KeyError) as e:
                print(f"  (cannot read {p}: {e})")
                _CACHE[k] = None
        r = _CACHE[k]
        if r is not None and ("hot", 0) in r.cells:
            out.append(r)
    return out


def boot_ci(x, stat=np.median, n=1000, seed=0):
    x = np.asarray(x, float)
    if len(x) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    v = np.array([stat(x[rng.integers(0, len(x), len(x))]) for _ in range(n)])
    return float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))


def med(v):
    return float(np.median(v)) if len(v) else float("nan")


# ---------------------------------------------------------------- 1. how we wait
POL_INTEL = [("0", "POLL only"), ("1", "up to C1"), ("3", "up to C3"), ("8", "default (C6+)")]


def fig01(a):
    name = "fig01_how_we_wait.png"
    # (group, label, run pattern, mode, Δt)
    rows = [
        ("no warm-up", "sleep, default idle (C6+)", "s8-r*", "none"),
        ("no warm-up", "sleep, idle up to C3", "s3-r*", "none"),
        ("no warm-up", "sleep, idle up to C1", "s1-r*", "none"),
        ("no warm-up", "sleep, POLL only", "s0-r*", "none"),
        ("no warm-up", "busy-wait loop", "s8-r*", "spin"),
        ("with warm-up", "sleep (default) + shadow every 1 ms", "s8-r*", "shadow"),
        ("with warm-up", "sleep (default) + shadow every 100 µs", "e8-t100-r*", "shadow"),
        ("with warm-up", "sleep (POLL) + shadow every 1 ms", "s0-r*", "shadow"),
        ("with warm-up", "spin + shadow every 1 ms", "s8-r*", "spinshadow"),
        ("with warm-up", "spin + shadow every 50 µs", "f0-ss50", "spinshadow"),
        ("with warm-up", "prewake 200 µs early, warm-up at once", "s8-r*", "prewake"),
        ("with warm-up", "prewake 200 µs early, warm-up 2 µs before", "f8-lead2", "prewake"),
    ]
    dt = 10
    data = []
    for g, lab, pat, m in rows:
        rr = [r for r in runs(pat) if r.has(m, dt)]
        if not rr:
            print(f"  no {pat} / {m}")
            continue
        e2e = [q(r.e2e(m, dt), .5) for r in rr]
        pen = [r.pen(m, dt) for r in rr]
        e_all = np.concatenate([r.e2e(m, dt) for r in rr])
        p_all = np.concatenate([r.excess(m, dt) for r in rr])
        data.append(dict(g=g, lab=lab, m=m, n=len(rr), trials=len(p_all), e=med(e2e), p=med(pen),
                         e_lo=q(e_all, .1), e_hi=q(e_all, .9), p_lo=q(p_all, .1), p_hi=q(p_all, .9)))
    if len(data) < 2:
        return skip(name, f"no runs of the main series *-s{{0,1,3,8}}-r* in {ROOT}")
    hot = med([r.hot for r in runs("s8-r*")])

    # y positions with a header row per group
    ys, labels, heads = [], [], []
    y = 0
    for g in dict.fromkeys(d["g"] for d in data):
        heads.append((y, g))
        y += 1
        for d in data:
            if d["g"] == g:
                d["y"] = y
                y += 1
        y += 0.4
    ymax = y - 0.4

    fig, rect = frame(13.2, 7.4, "How we wait for the event: deep sleep costs ~100 µs of lateness, "
                      "spin + shadow every 50 µs is almost hot",
                      f"Intel i7-8700, pause Δt = {dt} ms. Dot: median of p50 over process runs; "
                      "bar: p10–p90 of all trials (~250 per run). Right: number of runs.",
                      left=3.05, right=0.75, bottom=0.75)
    gs = fig.add_gridspec(1, 2, wspace=0.08, **rect)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharey=ax1)
    for ax in (ax1, ax2):
        style(ax, grid="x")
        for d in data:
            ax.axhline(d["y"], color=GRID, linewidth=0.6, zorder=0)
    for d in data:
        c = col(d["m"])
        ax1.plot([d["e_lo"], d["e_hi"]], [d["y"]] * 2, color=c, linewidth=2, alpha=.55, solid_capstyle="round")
        ax1.plot(d["e"], d["y"], marker=mk(d["m"]), color=c, markersize=9, markeredgecolor=SURF,
                 markeredgewidth=1.5, zorder=3)
        ax2.plot([d["p_lo"], d["p_hi"]], [d["y"]] * 2, color=c, linewidth=2, alpha=.55, solid_capstyle="round")
        ax2.plot(d["p"], d["y"], marker=mk(d["m"]), color=c, markersize=9, markeredgecolor=SURF,
                 markeredgewidth=1.5, zorder=3)
        ax2.text(1.015, d["y"], f"{d['n']}", transform=ax2.get_yaxis_transform(), va="center", ha="left",
                 fontsize=9, color=MUTED)
    ax2.text(1.015, heads[0][0], "runs", transform=ax2.get_yaxis_transform(), va="center", ha="left",
             fontsize=8.5, color=MUTED)
    # selective value labels: the extremes and the recommended option
    for d in data:
        if d["lab"] in ("sleep, default idle (C6+)", "spin + shadow every 50 µs", "busy-wait loop",
                        "sleep, POLL only"):
            ax1.annotate(f"{num(d['e'], 2 if d['e'] < 10 else 0)} µs", (d["e_hi"], d["y"]), xytext=(6, 0),
                         textcoords="offset points", va="center", fontsize=8.5, color=INK)
            ax2.annotate(f"{num(d['p'])} ns", (max(d["p_hi"], d["p"]), d["y"]), xytext=(6, 0),
                         textcoords="offset points", va="center", fontsize=8.5, color=INK)
    log_axis(ax1, "x")
    ax1.set_xlim(0.08, 600)
    ax1.axvline(hot / 1000, color=MUTED, linewidth=1.2, zorder=1)
    ax1.text(hot / 1000 * 1.12, -0.35, f"hot call {num(hot / 1000, 2)} µs", fontsize=8.5,
             color=INK2, va="center")
    ax2.axvline(0, color=MUTED, linewidth=1.2, zorder=1)
    ax2.text(12, -0.35, "0 = hot", fontsize=8.5, color=INK2, va="center")
    ax2.set_xlim(-60, 1180)
    ax1.set_xlabel("event → end of the first call, µs (log scale)")
    ax2.set_xlabel("first-call penalty vs hot, ns")
    ax1.set_title("a) Latency: wake-up lateness + first call")
    ax2.set_title("b) First-call penalty")
    ax1.set_ylim(ymax + 0.2, -0.8)
    ticks = [h[0] for h in heads] + [d["y"] for d in data]
    labs = [h[1] for h in heads] + [d["lab"] for d in data]
    ax1.set_yticks(ticks, labs)
    for t, lab in zip(ax1.get_yticklabels(), labs):
        t.set_color(INK)
        if lab in [h[1] for h in heads]:
            t.set_fontweight("bold")
            t.set_color(INK2)
    ax1.tick_params(axis="y", length=0, labelsize=9.5)
    plt.setp(ax2.get_yticklabels(), visible=False)
    ax2.tick_params(axis="y", length=0)
    used = list(dict.fromkeys(d["m"] for d in data))
    legend(fig, [key(m, line=False) for m in used])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 2. cooling curve
def fig02(a):
    name = "fig02_cooling_curve.png"
    modes = ["none", "shadow", "spin", "spinshadow", "prewake"]
    pols = [(p, t, runs(f"s{p}-r*")) for p, t in POL_INTEL]
    pols = [x for x in pols if x[2]]
    if not pols:
        return skip(name, "no runs *-s{0,1,3,8}-r*")
    fig, rect = frame(14, 5.8, "Cooling: after sleep the first call is set by idle depth, not pause length; "
                      "the busy-wait loop cools slowly",
                      "Intel i7-8700. Line: median of p50 over runs; band: p10–p90 of trials. "
                      "shadow every 1 ms; prewake: wake 200 µs early and warm up. "
                      "Grey line: hot call.",
                      left=0.85, bottom=0.75)
    gs = fig.add_gridspec(1, len(pols), wspace=0.08, **rect)
    ax0 = None
    for i, (p, title, rr) in enumerate(pols):
        ax = fig.add_subplot(gs[i], sharey=ax0) if ax0 else fig.add_subplot(gs[i])
        ax0 = ax0 or ax
        style(ax, grid="both")
        dts = sorted({k[1] for r in rr for k in r.cells if k[0] in modes})
        hot = med([r.hot for r in rr])
        ax.axhline(hot, color=MUTED, linewidth=1.5, zorder=1)
        for m in modes:
            xs = [dt for dt in dts if all(r.has(m, dt) for r in rr)]
            if not xs:
                continue
            ymed = [med([q(r.ns(m, dt), .5) for r in rr]) for dt in xs]
            pooled = [np.concatenate([r.ns(m, dt) for r in rr]) for dt in xs]
            ax.fill_between(xs, [q(v, .1) for v in pooled], [q(v, .9) for v in pooled], color=col(m),
                            alpha=.08, linewidth=0)
            ax.plot(xs, ymed, color=col(m), marker=mk(m), markersize=8, markeredgecolor=SURF,
                    markeredgewidth=1.5, zorder=4 if m == "none" else 3)
            if m == "shadow" and p in ("3", "8"):
                ax.annotate("sleep + shadow = sleep, no warm-up", (xs[1], ymed[1]), xytext=(0, 12),
                            textcoords="offset points", ha="center", fontsize=8.5, color=INK)
        log_axis(ax, "x")
        log_axis(ax, "y")
        ax.set_xticks(dts, [num(d) for d in dts])
        ax.set_xlim(min(dts) / 1.5, max(dts) * 1.5)
        ax.set_title(f"{title}  ·  {len(rr)} runs")
        ax.set_xlabel("pause Δt, ms")
        if i:
            plt.setp(ax.get_yticklabels(), visible=False)
        else:
            ax.set_ylabel("first call, ns (log scale)")
            ax.text(min(dts) / 1.4, hot * 0.93, f"hot {num(hot)} ns", fontsize=8.5, color=INK2, va="top")
    ax0.set_ylim(95, 1300)
    ax0.yaxis.set_major_locator(ticker.FixedLocator([100, 150, 200, 300, 500, 700, 1000]))
    legend(fig, [key(m) for m in modes])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 3. warm-up curve
def fig03(a):
    name = "fig03_warmup_curve.png"
    modes = ["none", "shadow", "spin", "spinshadow", "prewake"]
    dt = 10
    pols = [(p, t, runs(f"s{p}-r*")) for p, t in POL_INTEL]
    pols = [x for x in pols if x[2]]
    if not pols:
        return skip(name, "no runs *-s{0,1,3,8}-r*")
    floor = 1.0
    fig, rect = frame(14, 5.8, "Warm-up: the first call pays almost the whole penalty; after C6, calls 3–8 "
                      "are still ~15 ns slower than hot",
                      f"Intel i7-8700, Δt = {dt} ms. Penalty of call k = p50(k-th call after the pause) − "
                      "p50(k-th hot call of the same run). Line: median over runs; band: spread "
                      f"between runs.\nLog scale: values below {num(floor)} ns (i.e. “as hot”) "
                      "are clamped to the bottom edge.", left=0.85, bottom=0.75)
    gs = fig.add_gridspec(1, len(pols), wspace=0.08, **rect)
    ax0 = None
    calls = np.arange(1, 9)
    for i, (p, title, rr) in enumerate(pols):
        ax = fig.add_subplot(gs[i], sharey=ax0) if ax0 else fig.add_subplot(gs[i])
        ax0 = ax0 or ax
        style(ax, grid="both")
        for m in modes:
            if not all(r.has(m, dt) and r.has("hot", 0) for r in rr):
                continue
            per = np.array([[q(r.cells[(m, dt)]["calls"][k], .5) - q(r.cells[("hot", 0)]["calls"][k], .5)
                             for k in calls] for r in rr])
            per = np.maximum(per, floor)
            ax.fill_between(calls, per.min(0), per.max(0), color=col(m), alpha=.10, linewidth=0)
            ax.plot(calls, np.median(per, 0), color=col(m), marker=mk(m), markersize=7.5, markeredgecolor=SURF,
                    markeredgewidth=1.2, zorder=4 if m == "none" else 3)
        log_axis(ax, "y")
        ax.set_xticks(calls)
        ax.set_xlim(0.6, 8.4)
        ax.set_title(f"{title}  ·  {len(rr)} runs")
        ax.set_xlabel("call number after the pause")
        if i:
            plt.setp(ax.get_yticklabels(), visible=False)
        else:
            ax.set_ylabel("penalty of call k vs hot call k, ns (log)")
    ax0.set_ylim(floor * 0.85, 1500)
    ax0.yaxis.set_major_locator(ticker.FixedLocator([1, 3, 10, 30, 100, 300, 1000]))
    legend(fig, [key(m) for m in modes])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 4. shadow period in the spin loop
def fig04(a):
    name = "fig04_spin_shadow_period.png"
    dt = 10
    # T, µs → runs (policy POLL). spin and spinshadow: f0-ss*, pC0-T* and pC0-r*, series s0 for T = 1 ms;
    # spinpf: pC0-T*, pC0-r* (T = 50 µs). The -c/-d prefetch-set runs are left out.
    by_t = defaultdict(list)
    for r in runs("f0-ss*"):
        m = re.fullmatch(r"f0-ss(\d+)", r.name)
        if m:
            by_t[int(m.group(1))].append(r)
    for r in runs("pC0-T*"):
        m = re.fullmatch(r"pC0-T(\d+)", r.name)
        if m:
            by_t[int(m.group(1))].append(r)
    by_t[50] += runs("pC0-r*")
    by_t[1000] += runs("s0-r*")
    by_t = {t: v for t, v in by_t.items() if v}
    if len(by_t) < 3:
        return skip(name, "no period sweep: runs *-f0-ss* / *-pC0-T*")
    ts = sorted(by_t)
    fig, rect = frame(10.5, 6.2, "Shadow in the spin loop: a period ≤ 50 µs removes almost the whole penalty, "
                      "prefetch only half",
                      f"Intel i7-8700, POLL policy, Δt = {dt} ms. Dot: p50 penalty over all trials of the "
                      "cell; bar: bootstrap 95% CI.\nBelow the axis: number of runs and the cost of shadow "
                      "(share of the core spent on warm-ups).", left=1.75, right=0.35, bottom=1.35, panel_titles=False)
    ax = fig.add_subplot(fig.add_gridspec(1, 1, **rect)[0])
    style(ax, grid="y")
    ax.axvspan(100 / 1.25, ts[-1] * 2, color=GRID, alpha=.35, linewidth=0, zorder=0)
    ax.text(105, 196, "period longer than the spin-loop cooling time (~100 µs)", fontsize=8.5, color=INK2, va="top")
    ax.axhline(0, color=MUTED, linewidth=1.2, zorder=1)
    ax.text(ts[-1] * 1.5, 3, "0 = hot", fontsize=8.5, color=INK2, va="bottom", ha="right")
    ends = {}
    for m in ["spin", "spinpf", "spinshadow"]:
        xs, ys, lo, hi = [], [], [], []
        for t in ts:
            rr = [r for r in by_t[t] if r.has(m, dt)]
            if not rr:
                continue
            ex = np.concatenate([r.excess(m, dt) for r in rr])
            l, h = boot_ci(ex, lambda v: q(v, .5))
            xs.append(t), ys.append(q(ex, .5)), lo.append(l), hi.append(h)
        if not xs:
            continue
        ax.plot(xs, ys, color=col(m), marker=mk(m), markersize=8, markeredgecolor=SURF, markeredgewidth=1.5,
                zorder=3)
        ax.vlines(xs, lo, hi, color=col(m), linewidth=2, alpha=.6, zorder=2)
        ends[m] = dict(zip(xs, ys))
    labels = {"spin": "spin, no warm-up", "spinshadow": "spin + shadow every T", "spinpf": "spin + prefetch every T"}
    # direct labels above the line between T = 20 and 50 µs, where the three lines are well apart
    for m, pts in ends.items():
        if 20 in pts and 50 in pts:
            x = (20 * 50) ** .5
            ax.annotate(labels[m], (x, (pts[20] + pts[50]) / 2), xytext=(0, 9), textcoords="offset points",
                        ha="center", va="bottom", fontsize=9, color=INK)
    log_axis(ax, "x")
    ax.set_xticks(ts, [num(t) for t in ts])
    ax.set_xlim(ts[0] / 1.25, ts[-1] * 1.6)
    ax.set_ylim(-12, 200)
    ax.set_xlabel("warm-up period T, µs (log scale)")
    ax.set_ylabel("first-call penalty vs hot, ns")
    # cost of shadow and run counts under the axis
    tr = ax.get_xaxis_transform()
    ax.text(ts[0] / 1.25, -0.20, "runs", transform=tr, ha="right", va="top", fontsize=8.5, color=MUTED)
    ax.text(ts[0] / 1.25, -0.27, "shadow cost, % of core", transform=tr, ha="right", va="top", fontsize=8.5,
            color=MUTED)
    for t in ts:
        rr = [r for r in by_t[t] if r.has("spinshadow", dt)]
        if not rr:
            continue
        cost = 100 * np.mean(np.concatenate([r.cells[("spinshadow", dt)]["warm_us"] for r in rr])) / (dt * 1000)
        ax.text(t, -0.20, f"{len(by_t[t])}", transform=tr, ha="center", va="top", fontsize=8.5, color=INK2)
        ax.text(t, -0.27, num(cost, 2 if cost < 0.1 else 1), transform=tr, ha="center", va="top", fontsize=8.5,
                color=INK2)
    legend(fig, [key("spin", labels["spin"]), key("spinpf", labels["spinpf"]),
                 key("spinshadow", labels["spinshadow"])])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 5. prefetch decomposition
def fig05(a):
    name = "fig05_prefetch_decomposition.png"
    dt = 10
    pols = []
    for pol, title in (("3", "after sleep up to C3"), ("8", "after sleep in C6+ (default)")):
        by_set = {s: runs(f"pA{pol}-{s}-r*") for s in ("0", "d", "c", "dc")}
        allr = [r for v in by_set.values() for r in v]
        if allr:
            pols.append((title, by_set, allr))
    if not pols:
        return skip(name, "no runs *-pA{3,8}-{0,d,c,dc}-r*")
    fig, rect = frame(14.5, 6.6, "Prefetch after sleep brings back data and code up to L2, "
                      "but not the frontend: only execution (prewake) restores it",
                      f"Intel i7-8700, Δt = {dt} ms. Bar: median of the p50 penalty over runs; whisker: "
                      "spread between runs (5 per set, ~950 trials each).\npfwake and prewake: "
                      "wake 200 µs early; prefetch 20 µs before the event. Right: breakdown from “empty” "
                      "(wake up and do nothing) to prewake.", left=0.95, right=1.65, bottom=0.85)
    gs = fig.add_gridspec(1, len(pols), wspace=0.30, **rect)
    cats = [("none", None, "sleep, no\nwarm-up"), ("pfwake", "0", "pfwake:\nempty"),
            ("pfwake", "d", "pfwake:\ndata"), ("pfwake", "c", "pfwake:\ncode"),
            ("pfwake", "dc", "pfwake:\ncode+data"), ("prewake", None, "prewake")]
    ax0 = None
    for i, (title, by_set, allr) in enumerate(pols):
        ax = fig.add_subplot(gs[i], sharey=ax0) if ax0 else fig.add_subplot(gs[i])
        ax0 = ax0 or ax
        style(ax, grid="y")
        vals = {}
        for j, (m, s, lab) in enumerate(cats):
            rr = [r for r in (allr if s is None else by_set[s]) if r.has(m, dt)]
            if not rr:
                continue
            p = [r.pen(m, dt) for r in rr]
            v = med(p)
            vals[(m, s)] = v
            ax.bar(j, v, width=0.62, color=col(m), alpha=.45 if s == "0" else 1, linewidth=0, zorder=2)
            ax.vlines(j, min(p), max(p), color=INK2, linewidth=1.2, zorder=3)
            ax.text(j, max(max(p), v) + 18, f"{num(v)}", ha="center", va="bottom", fontsize=9, color=INK)
        ax.axhline(0, color=AXIS, linewidth=1, zorder=1)
        ax.set_xticks(range(len(cats)), [c[2] for c in cats], fontsize=8.8)
        ax.tick_params(axis="x", length=0)
        ax.set_title(f"{['a', 'b'][i]}) {title}")
        if i == 0:
            ax.set_ylabel("first-call penalty vs hot, ns")
        else:
            plt.setp(ax.get_yticklabels(), visible=False)
        # the three lumps: control → data → data+code → prewake, as a ruler right of the bars
        need = [("pfwake", "0"), ("pfwake", "d"), ("pfwake", "dc"), ("prewake", None)]
        if all(k in vals for k in need):
            y0, yd, ydc, ypw = (vals[k] for k in need)
            x = len(cats) - 0.15
            lumps = [(y0, yd, "data in L1d", "#9a9893"), (yd, ydc, "code in L2", "#6f6e6a"),
                     (ydc, ypw, "frontend remainder:\nL1i, BTB, predictor", "#3d3c3a")]
            for top, bot, lab, shade in lumps:
                ax.plot([x, x], [bot + 4, top - 4], color=shade, linewidth=4, solid_capstyle="butt", zorder=3)
                ax.text(x + 0.18, (top + bot) / 2, f"{lab}\n−{num(top - bot)} ns", va="center", ha="left",
                        fontsize=8.5, color=INK, linespacing=1.25)
            for yy, j in ((y0, 1), (yd, 2), (ydc, 4), (ypw, 5)):
                ax.plot([j + 0.31, x], [yy, yy], color=AXIS, linewidth=0.8, zorder=1)
        ax.set_xlim(-0.6, len(cats) - 0.05)
    ax0.set_ylim(0, 1120)
    legend(fig, [key("none", line=False, marker="s", markersize=10),
                 key("pfwake", "pfwake: wake early and prefetch (pale: empty set, control)",
                     line=False, marker="s", markersize=10),
                 key("prewake", line=False, marker="s", markersize=10)])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 6. counters after C6
def fig06(a):
    name = "fig06_counters_c6.png"
    dt = 10
    panels = [("ic_miss", "b", "Instruction cache misses (L1i)", "per call"),
              ("baclears", "c", "Fetch restarts due to the BTB", "per call"),
              ("br_miss", "a", "Branch mispredictions", "per call"),
              ("dsb_uops", "d", "Micro-ops from the DSB", "per call; higher is better")]
    cells = [("hot", 0, "0", "hot"), ("none", dt, "0", "sleep"), ("pfwake", dt, "dc", "pfwake\ncode+data"),
             ("prewake", dt, "0", "prewake")]
    found = {}
    for cnt, g, *_ in panels:
        for m, d, s, _ in cells:
            for r in runs(f"pB8-{s}-{g}"):
                if r.has(m, d) and cnt in r.cells[(m, d)]:
                    found[(cnt, m)] = r.cells[(m, d)][cnt]
    if len(found) < 4:
        return skip(name, "no counter runs *-pB8-{0,dc}-{a,b,c,d}")
    fig, rect = frame(13.5, 5.0, "After C6, prefetching code and data does not help the frontend: L1i, BTB and "
                      "predictor are as after sleep",
                      f"Intel i7-8700, sleep in C6+ (default policy), Δt = {dt} ms; counters via rdpmc, "
                      "first call. Bar: mean (~250 trials of one run); whisker: bootstrap "
                      "95% CI of the mean.\nEach panel has its own scale.", left=0.6, bottom=0.85)
    gs = fig.add_gridspec(1, len(panels), wspace=0.32, **rect)
    for i, (cnt, g, title, unit) in enumerate(panels):
        ax = fig.add_subplot(gs[i])
        style(ax, grid="y")
        top = 0
        for j, (m, d, s, lab) in enumerate(cells):
            v = found.get((cnt, m))
            if v is None:
                continue
            mu = float(np.mean(v))
            lo, hi = boot_ci(v, np.mean)
            ax.bar(j, mu, width=0.62, color=col(m), linewidth=0, zorder=2)
            ax.vlines(j, lo, hi, color=INK2, linewidth=1.2, zorder=3)
            top = max(top, hi)
            ax.annotate(num(mu, 1 if mu < 10 else 0), (j, hi), xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=9, color=INK)
        ax.set_ylim(0, top * 1.15)
        ax.set_xticks(range(len(cells)), [c[3] for c in cells], fontsize=8.8)
        ax.tick_params(axis="x", length=0)
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(unit)
        ax.axhline(0, color=AXIS, linewidth=1)
    legend(fig, [key(m, lab.replace("\n", " ") if m == "pfwake" else None, line=False, marker="s", markersize=10)
                 for m, _, _, lab in cells])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 7. AMD vs Intel
POL_AMD = [("0", "POLL only"), ("1", "up to C1"), ("2", "up to C2"), ("9", "default")]


def amd_runs(pattern, root):
    """AMD runs from root and from results/ (if copied there later), time in exact core cycles."""
    seen, out = set(), []
    for d in (root, ROOT):
        if d and os.path.isdir(d):
            for r in runs(pattern, d, cycles_time=True):
                if r.name not in seen and r.ghz:
                    seen.add(r.name)
                    out.append(r)
    return out


def machine_panels(fig, rect, groups, policies, xt, label_last=True):
    """Small multiples: hot time, none / spin / prewake penalty (shared scale), sleep lateness.
    groups: [(label, marker style dict, {policy index: [Run]})]."""
    dt = 10
    panels = [("hot", "Hot call, ns", "time"), ("none", "Sleep, no warm-up: penalty, ns", "pen"),
              ("spin", "Busy-wait loop: penalty, ns", "pen"), ("prewake", "Prewake: penalty, ns", "pen"),
              ("none", "Sleep: wake-up lateness, µs", "late")]
    gs = fig.add_gridspec(1, len(panels), wspace=0.30, **rect)
    pen_axes = []
    off = np.linspace(-0.17, 0.17, len(groups)) if len(groups) > 1 else [0]
    for i, (m, title, kind) in enumerate(panels):
        ax = fig.add_subplot(gs[i], sharey=pen_axes[0] if kind == "pen" and pen_axes else None)
        if kind == "pen":
            pen_axes.append(ax)
        style(ax, grid="y")
        for (glab, sty, byp), o in zip(groups, off):
            xs, ys, lo, hi = [], [], [], []
            for k in range(len(policies)):
                rr = [r for r in byp.get(k, []) if r.has(m, 0 if kind == "time" else dt)]
                if not rr:
                    continue
                if kind == "time":
                    v = [r.hot for r in rr]
                elif kind == "pen":
                    v = [r.pen(m, dt) for r in rr]
                else:
                    v = [q(r.cells[(m, dt)]["late_us"], .5) for r in rr]
                xs.append(k + o), ys.append(med(v)), lo.append(min(v)), hi.append(max(v))
            if not xs:
                continue
            c = col(m)
            ax.vlines(xs, lo, hi, color=c, linewidth=2, alpha=.6, zorder=2)
            ax.plot(xs, ys, linestyle="none", marker=sty["marker"], markersize=9,
                    markerfacecolor=c if sty["fill"] else SURF, markeredgecolor=c,
                    markeredgewidth=1.8 if not sty["fill"] else 1.2, zorder=3)
            if label_last and xs and round(xs[-1] - o) == len(policies) - 1:
                v = ys[-1]
                txt = num(v, 1 if (kind == "late" and v < 10) else 0)
                ax.annotate(txt, (xs[-1], v), xytext=(-8 if sty["fill"] else 8, 0), textcoords="offset points",
                            ha="right" if sty["fill"] else "left", va="center", fontsize=8.5, color=INK)
        ax.set_xticks(range(len(policies)), xt, fontsize=8.6)
        ax.set_xlim(-0.5, len(policies) - 0.5)
        ax.tick_params(axis="x", length=0)
        ax.set_title(title, fontsize=10)
        if kind == "late":
            log_axis(ax, "y")
            ax.set_ylim(0.05, 300)
        if kind == "time":
            ax.set_ylim(0, ax.get_ylim()[1] * 1.12)
        if kind == "pen" and len(pen_axes) > 1:
            plt.setp(ax.get_yticklabels(), visible=False)
    return pen_axes


def fig07a(a):
    name = "fig07a_amd_vs_intel.png"
    amd = {k: amd_runs(f"z1-p{p}-r*", a.amd) for k, (p, _) in enumerate(POL_AMD)}
    intel = {k: runs(f"s{p}-r*") for k, (p, _) in enumerate(POL_INTEL)}
    if not any(amd.values()):
        return skip(name, f"no AMD runs *-z1-p*-r* in {a.amd} or {ROOT}")
    if not any(intel.values()):
        return skip(name, "no Intel runs *-s{0,1,3,8}-r*")
    ghz = med([r.ghz for v in amd.values() for r in v])
    fig, rect = frame(15.5, 5.4, "AMD Zen 5 vs Intel: on Zen 5, sleep and spinning barely cool the handler; "
                      "lateness ~10 µs under any policy",
                      "Δt = 10 ms, the same cells. Dot: median of p50 over runs (Intel 5, 2 for C3; AMD 2); "
                      "bar: spread between runs. AMD time from core cycles, "
                      f"frequency from hot calls ≈ {num(ghz, 2)} GHz.\nThird step: C3 on Intel, C2 on AMD. "
                      "prewake: wake δ early (Intel 200 µs; AMD: δ of the amd-c1.sh series, 100 µs by default).",
                      left=0.6, bottom=0.85)
    xt = ["POLL", "C1", "C3 · C2", "default"]
    groups = [("Intel i7-8700", dict(marker="o", fill=True), intel),
              ("AMD Zen 5", dict(marker="D", fill=False), amd)]
    machine_panels(fig, rect, groups, POL_INTEL, xt)
    legend(fig, [Line2D([], [], color=INK2, marker="o", linestyle="none", markersize=9, label="Intel i7-8700"),
                 Line2D([], [], color=INK2, marker="D", linestyle="none", markersize=9, markerfacecolor=SURF,
                        markeredgewidth=1.8, label="AMD Zen 5 (hollow)"),
                 Line2D([], [], color=SURF, label="colour = wait mode, as in the other figures")])
    return save(fig, name, a.out)


def fig07b(a):
    name = "fig07b_amd_busy_neighbour.png"
    quiet = {k: amd_runs(f"z1-p{p}-r*", a.amd) for k, (p, _) in enumerate(POL_AMD)}
    busy = {k: amd_runs(f"z2-p{p}-nb-r*", a.amd) for k, (p, _) in enumerate(POL_AMD)}
    if not any(busy.values()) or not any(quiet.values()):
        return skip(name, f"no run pairs *-z1-p*-r* and *-z2-p*-nb-r* in {a.amd}")
    cold = med([r.pen("cold", 0) for v in list(quiet.values()) + list(busy.values()) for r in v if r.has("cold", 0)])
    cold = round(cold, -2)
    fig, rect = frame(15.5, 5.6, "AMD Zen 5: a busy neighbour on the same CCD changes almost nothing",
                      "Δt = 10 ms; neighbour: a core of the same CCD in an empty loop. Dot: median of p50 over 2 runs; "
                      "bar: spread between them. Time from core cycles.\nFor scale: evicting the caches "
                      f"with a 64 MB sweep gives a penalty of ~{num(cold)} ns here; "
                      "the penalties have their own scale, shared by the three panels.",
                      left=0.6, bottom=0.85)
    xt = ["POLL", "C1", "C2", "default"]
    groups = [("quiet", dict(marker="o", fill=True), quiet), ("neighbour busy", dict(marker="D", fill=False), busy)]
    machine_panels(fig, rect, groups, POL_AMD, xt, label_last=False)
    legend(fig, [Line2D([], [], color=INK2, marker="o", linestyle="none", markersize=9, label="neighbouring cores idle (z1)"),
                 Line2D([], [], color=INK2, marker="D", linestyle="none", markersize=9, markerfacecolor=SURF,
                        markeredgewidth=1.8, label="CCD neighbour spinning (z2, hollow)")])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- online stand (export.csv)
def read_export(path):
    """(series, arch, mode, metric) → sorted [(x, y, n)]; later duplicates win. The file may have no header."""
    if not os.path.exists(path):
        return None
    d = {}
    with open(path) as f:
        for r in csv.reader(f):
            if len(r) < 7 or r[0] == "series":
                continue
            try:
                d[(r[0], r[1], r[2], r[3], float(r[4]))] = (float(r[5]), int(float(r[6])))
            except ValueError:
                continue
    out = defaultdict(list)
    for (s, ar, m, met, x), (y, n) in d.items():
        out[(s, ar, m, met)].append((x, y, n))
    return {k: sorted(v) for k, v in out.items()}


def pick_series(ex, archs, want=None, metric="handler_bin_median"):
    """{arch: series} for the A/B figures. A night series may hold both architectures (L1) or be split
    by architecture (L1-A, L1-B): prefer one stem for both, then the largest total n; the nohz A/B series
    (NZ-*) are left out. want: 'NAME' or 'NAME_A,NAME_B'."""
    if want:
        names = want.split(",")
        return {ar: names[min(i, len(names) - 1)] for i, ar in enumerate(archs)}
    tot = defaultdict(lambda: defaultdict(int))
    for (s, ar, m, met), v in ex.items():
        if met == metric and not s.startswith("NZ-"):
            tot[ar][s] += sum(n for *_, n in v)
    if not all(tot.get(ar) for ar in archs):
        return None
    if len(archs) == 1:
        ar = archs[0]
        return {ar: max(tot[ar], key=tot[ar].get)}
    stem = lambda s: re.sub(r"-[AB]$", "", s)  # noqa: E731
    pairs = [(sa, sb) for sa in tot[archs[0]] for sb in tot[archs[1]]]
    best = max(pairs, key=lambda p: (stem(p[0]) == stem(p[1]), tot[archs[0]][p[0]] + tot[archs[1]][p[1]]))
    return dict(zip(archs, best))


def series_txt(S):
    names = list(dict.fromkeys(S.values()))
    return "series " + " / ".join(names)


def qname(e):
    """Quantile 1 − 10^-e as 'p99.9'."""
    e = int(round(e))
    return "p" + num(100 * (1 - 10 ** -e), max(0, e - 2))


def at(v, x0):
    """y of the point whose x is closest to x0 (log distance for x > 0)."""
    if not v:
        return float("nan"), float("nan")
    x, y, _ = min(v, key=lambda t: abs(np.log(t[0] / x0)) if t[0] > 0 and x0 > 0 else abs(t[0] - x0))
    return x, y


def pause_txt(ms):
    return f"{num(ms / 1000, 1)} s" if ms >= 1000 else f"{num(ms, 0 if ms >= 10 else 1)} ms"


def ccdf_axes(ax):
    log_axis(ax, "x")
    ax.xaxis.set_major_locator(ticker.LogLocator(subs=(1, 2, 5)))
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(ticker.FixedLocator([1e-1, 1e-2, 1e-3, 1e-4, 1e-5]))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(
        lambda v, _: {1e-1: "10%", 1e-2: "1%", 1e-3: "0.1%", 1e-4: "0.01%", 1e-5: "0.001%"}.get(round(v, 8), "")))
    ax.yaxis.set_minor_formatter(ticker.NullFormatter())


def need_online(a, name):
    ex = read_export(a.online)
    if ex is None:
        skip(name, f"no {a.online} (export.py over the online-stand series)")
    return ex


def end_labels(ax, items, min_gap_pt=11):
    """Direct labels at line ends — only if they do not collide (otherwise the legend carries identity)."""
    if not items:
        return
    fig = ax.figure
    fig.canvas.draw()
    pts = sorted((ax.transData.transform((x, y))[1], x, y, t) for x, y, t in items)
    gaps = [b[0] - a_[0] for a_, b in zip(pts, pts[1:])]
    if gaps and min(gaps) * 72 / fig.dpi < min_gap_pt:
        return
    for _, x, y, t in pts:
        ax.annotate(t, (x, y), xytext=(7, 0), textcoords="offset points", fontsize=9, color=INK, va="center")


AB_KEYS = [Line2D([], [], color=INK2, linestyle="-", marker="o", markersize=8, markeredgecolor=SURF,
                  label="A: the receiver gets the whole feed"),
           Line2D([], [], color=INK2, linestyle="--", marker="o", markersize=8, markerfacecolor=SURF,
                  markeredgewidth=1.4, label="B: only the quiet pair"),
           Line2D([], [], color=SURF, label="colour and shape = wait mode")]


def fig08(a):
    name = "fig08_online_system_ccdf.png"
    ex = need_online(a, name)
    if ex is None:
        return
    S = pick_series(ex, ["A"], a.series, "system_ccdf")
    s = S["A"] if S else None
    modes = [m for m in ONLINE_ORDER if (s, "A", m, "system_ccdf") in ex]
    if not modes:
        return skip(name, "export.csv has no system_ccdf for architecture A")
    curves = {m: ex[(s, "A", m, "system_ccdf")] for m in modes}
    e = min(3, int(min(v[-1][0] for v in curves.values())))
    tail = {m: at(v, e)[1] for m, v in curves.items()}
    lo, hi = min(tail, key=tail.get), max(tail, key=tail.get)
    short = lambda m: ONLINE_NAME[m].replace(" (spin)", "")  # noqa: E731
    title = (f"Architecture A, system latency: {qname(e)} from {num(tail[lo], 1 if tail[lo] < 10 else 0)} µs "
             f"({short(lo)}) to {num(tail[hi], 1 if tail[hi] < 10 else 0)} µs ({short(hi)})")
    same = []  # pairs of curves that coincide (≤ 3% apart at every common quantile): say so, one hides the other
    for i, m1 in enumerate(modes):
        for m2 in modes[i + 1:]:
            c1, c2 = dict((e_, y) for e_, y, _ in curves[m1]), dict((e_, y) for e_, y, _ in curves[m2])
            com = [e_ for e_ in c1 if e_ in c2]
            if com and max(abs(c1[e_] - c2[e_]) / c1[e_] for e_ in com) <= 0.03:
                same.append(f"“{short(m1)}” and “{short(m2)}”")
    same_txt = (" Curves " + ", ".join(same) + " coincide (≤ 3%).") if same else ""
    n = max(v[0][2] for v in curves.values())
    fig, rect = frame(10, 6.2, title,
                      f"Online stand, series {s}; all pairs except the observed one; up to {n:,} packets per mode. "
                      + "Latency: OS kernel timestamp → end of the handler.\nCurve: share of packets slower than x "
                      "(quantiles from export.csv)." + same_txt, left=1.0, bottom=0.75, panel_titles=False)
    ax = fig.add_subplot(fig.add_gridspec(1, 1, **rect)[0])
    style(ax, grid="both")
    ends = []
    for m, v in curves.items():
        x = [y for _, y, _ in v]
        p = [10 ** -e_ for e_, _, _ in v]
        ax.plot(x, p, color=col(m), marker=mk(m), markersize=8, markevery=5, markeredgecolor=SURF,
                markeredgewidth=1.2)
        ends.append((x[-1], p[-1], ONLINE_NAME[m]))
    ccdf_axes(ax)
    ax.set_xlabel("latency, µs (log scale)")
    ax.set_ylabel("share of packets slower than x (log scale)")
    if len(modes) <= 4:
        end_labels(ax, ends)
    legend(fig, [key(m, ONLINE_NAME[m]) for m in modes])
    return save(fig, name, a.out)


def online_ab(a, name, title_fn, sub, xlab, ylab, ccdf):
    ex = need_online(a, name)
    if ex is None:
        return
    base = "rare_ccdf" if ccdf else "handler_bin_median"
    S = pick_series(ex, ["A", "B"], a.series, base)
    if not S:
        return skip(name, f"export.csv has no {base} for architectures A and B")
    modes = [m for m in ONLINE_ORDER if any((S[ar], ar, m, base) in ex for ar in ("A", "B"))]
    w = 3.1 * len(modes) + 1.2
    fig, rect = frame(w, 5.2, title_fn(ex, S, modes), f"Online stand, {series_txt(S)}. {sub}", left=0.95, bottom=0.75)
    gs = fig.add_gridspec(1, len(modes), wspace=0.08, **rect)
    ax0 = None
    style_ab = {"A": dict(ls="-", fill=True), "B": dict(ls="--", fill=False)}
    for i, m in enumerate(modes):
        ax = fig.add_subplot(gs[i], sharey=ax0, sharex=ax0) if ax0 else fig.add_subplot(gs[i])
        ax0 = ax0 or ax
        style(ax, grid="both")
        for ar in ("A", "B"):
            v = ex.get((S[ar], ar, m, base))
            if not v:
                continue
            st = style_ab[ar]
            kw = dict(color=col(m), linestyle=st["ls"], marker=mk(m), markersize=8, markeredgewidth=1.4,
                      markerfacecolor=col(m) if st["fill"] else SURF,
                      markeredgecolor=SURF if st["fill"] else col(m))
            if ccdf:
                ax.plot([y for _, y, _ in v], [10 ** -e for e, _, _ in v], markevery=6, **kw)
            else:
                x = [x for x, _, _ in v]
                ax.plot(x, [y for _, y, _ in v], markevery=3, **kw)
                hi = dict((x_, y_) for x_, y_, _ in ex.get((S[ar], ar, m, "handler_bin_p90"), []))
                if hi:
                    xs = [x_ for x_ in x if x_ in hi]
                    lo = dict((x_, y_) for x_, y_, _ in v)
                    ax.fill_between(xs, [lo[x_] for x_ in xs], [hi[x_] for x_ in xs], color=col(m), alpha=.08,
                                    linewidth=0)
        if ccdf:
            ccdf_axes(ax)
        else:
            log_axis(ax, "x")
        ax.set_title(ONLINE_NAME[m])
        ax.set_xlabel(xlab)
        if i:
            plt.setp(ax.get_yticklabels(), visible=False)
        else:
            ax.set_ylabel(ylab)
    legend(fig, AB_KEYS)
    return save(fig, name, a.out)


def fig09(a):
    def title(ex, S, modes):
        e = 2
        d = []
        for m in modes:
            va, vb = ex.get((S["A"], "A", m, "rare_ccdf")), ex.get((S["B"], "B", m, "rare_ccdf"))
            if va and vb and va[-1][0] >= e and vb[-1][0] >= e:
                ya, yb = at(va, e)[1], at(vb, e)[1]
                d.append(100 * (yb - ya) / ya)
        if not d:
            return "Online stand: latency tail of the quiet pair, architecture A vs B"
        word = "lower" if max(d) < 0 else "higher" if min(d) > 0 else "lower to higher"
        lo_, hi_ = sorted((num(abs(min(d))), num(abs(max(d)))), key=float)
        rng = f"{lo_}–{hi_}%" if lo_ != hi_ else f"~{lo_}%"
        if min(d) < 0 < max(d):
            rng = f"from −{num(abs(min(d)))} to +{num(max(d))}%"
            return f"Quiet pair: {qname(e)} latency in B vs A, {rng} depending on the wait mode"
        return f"Quiet pair: {qname(e)} latency is {rng} {word} in B than in A, depending on the wait mode"
    return online_ab(a, "fig09_online_rare_ccdf_ab.png", title,
                     "Latency: OS kernel timestamp → end of the handler; curve: share of packets slower than x.",
                     "latency, µs (log)", "share of packets slower than x (log)", True)


def fig10(a):
    def title(ex, S, modes):
        ar = "B" if any((S["B"], "B", m, "handler_bin_median") in ex for m in modes) else "A"
        g = {}
        for m in modes:
            v = ex.get((S[ar], ar, m, "handler_bin_median"))
            if v and len(v) > 2:
                g[m] = (at(v, 0.1)[1], at(v, 1000)[1])
        if "spin" in g and "spinshadow" in g:
            sp, ss = g["spin"], g["spinshadow"]
            return (f"The handler cools with the pause: from 0.1 ms to ~1 s, spin {num(sp[0])} → {num(sp[1])} ns, "
                    f"spin + shadow {num(ss[0])} → {num(ss[1])} ns ({ar})")
        return "Online stand: handler time vs the pause before the packet, A vs B"
    return online_ab(a, "fig10_online_handler_vs_pause_ab.png", title,
                     "Quiet-pair packets binned by the pause before them (log bins). Line: median; band: "
                     "median to p90.", "pause before the packet, ms (log)", "handler time, ns", False)


def fig11(a):
    name = "fig11_nohz_full.png"
    ex = need_online(a, name)
    if ex is None:
        return
    conds = [("NZ-A", "A: no nohz_full", "A", "-", "o", True), ("NZ-B", "B: nohz_full=2-5", "B", "--", "s", False),
             ("NZ-B-tick", "B + tick restored", "B + tick", ":", "^", False)]

    def curve(series, m, metric="handler_bin_median"):
        k = [k for k in ex if k[0] == series and k[2] == m and k[3] == metric]
        return ex[k[0]] if k else None

    modes = [m for m in ("spin", "spinshadow") if any(curve(c[0], m) for c in conds)]
    if not modes:
        return skip(name, "export.csv has no NZ-A / NZ-B / NZ-B-tick series")
    # On the stand the tick was not stopped on the receiver cores in any condition (LOC ≈ 1000/s on the
    # spin / spinshadow cores in A, B and B-tick), so A/B/B-tick differences here are A/A noise. The real
    # nohz_full result is the bench A/B test, fig12.
    title = "Online stand: the tick did not stop on the receiver cores; differences are within A/A noise"
    fig, rect = frame(12, 5.6, title,
                      "Series NZ-A / NZ-B / NZ-B-tick: ~1000 ticks/s on the spin and spinshadow cores in all three "
                      "conditions (LOC counter); A/A spread ≈ 20–26 ns.\nQuiet-pair packets binned by the pause before them "
                      "(log bins); line: median handler time; band: median to p90 for condition A. "
                      "The clean nohz_full experiment is Fig. 12.", left=0.95, right=1.0, bottom=0.75)
    gs = fig.add_gridspec(1, len(modes), wspace=0.16, **rect)
    ax0, axes = None, []
    for i, m in enumerate(modes):
        ax = fig.add_subplot(gs[i], sharey=ax0, sharex=ax0) if ax0 else fig.add_subplot(gs[i])
        ax0 = ax0 or ax
        style(ax, grid="both")
        ends = []
        for s, lab, short, ls, marker, fill in conds:
            v = curve(s, m)
            if not v:
                continue
            x, y = [x for x, _, _ in v], [y for _, y, _ in v]
            ax.plot(x, y, color=col(m), linestyle=ls, marker=marker, markersize=8, markeredgewidth=1.4,
                    markevery=2, markerfacecolor=col(m) if fill else SURF, markeredgecolor=SURF if fill else col(m))
            if fill:
                hi = dict((x_, y_) for x_, y_, _ in (curve(s, m, "handler_bin_p90") or []))
                xs = [x_ for x_ in x if x_ in hi]
                ax.fill_between(xs, [dict(zip(x, y))[x_] for x_ in xs], [hi[x_] for x_ in xs], color=col(m),
                                alpha=.10, linewidth=0)
            ends.append((x[-1], y[-1], short))
        log_axis(ax, "x")
        ax.set_title(ONLINE_NAME[m])
        ax.set_xlabel("pause before the packet, ms (log)")
        if i:
            plt.setp(ax.get_yticklabels(), visible=False)
        else:
            ax.set_ylabel("handler time, ns")
        axes.append((ax, ends))
    for ax, ends in axes:
        end_labels(ax, ends)
    legend(fig, [Line2D([], [], color=INK2, linestyle=ls, marker=mk_, markersize=8, markeredgewidth=1.4,
                        markerfacecolor=INK2 if fill else SURF, markeredgecolor=SURF if fill else INK2, label=lab)
                 for _, lab, _, ls, mk_, fill in conds])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 12. nohz_full in bench
def fig12(a):
    name = "fig12_nohz_bench.png"
    conds = [("A", "A: normal boot (1000 Hz tick)", True), ("B", "B: nohz_full=2-5 rcu_nocbs=2-5", False)]
    data = {}
    for c, *_ in conds:
        short = runs(f"nz{c}-s[0-9]*")   # nz?-s1 (seed 1 and its repeat), nz?-s2: Δt = 1, 10, 100 ms
        long_ = runs(f"nz{c}-long")      # Δt = 1 s
        if short:
            data[c] = (short, long_)
    if len(data) < 2:
        return skip(name, "no runs *-nzA-s*, *-nzB-s* (and *-nz?-long)")
    title = "Without the timer tick the busy-wait loop does not cool with the pause: ~60 ns vs 87→271 ns"
    med_line = {}
    for c, (short, long_) in data.items():
        rr = short + long_
        dts = sorted({k[1] for r in rr for k in r.cells if k[0] == "spin"})
        med_line[c] = {dt: med([r.pen("spin", dt) for r in rr if r.has("spin", dt)]) for dt in dts}
    if all(c in med_line for c in "AB"):
        mA, mB = med_line["A"], med_line["B"]
        bvals = [v for dt, v in mB.items() if dt <= 100]
        title = (f"Without the timer tick the busy-wait loop does not cool with the pause: ~{num(round(med(bvals), -1))} ns vs "
                 f"{num(mA[min(mA)])}→{num(mA[max(mA)])} ns")
    # share of spin trials with no interrupt during the pause (gap_us = −1), B, Δt ≤ 100 ms, per run
    quiet, seen = [], set()
    for r in sorted(data["B"][0], key=lambda r: r.name):
        g = np.concatenate([r.cells[("spin", dt)]["gap_us"] for dt in (1, 10, 100) if r.has("spin", dt)])
        quiet.append((r.name + (" (repeat)" if r.name in seen else ""), 100 * float(np.mean(g < 0))))
        seen.add(r.name)
    ql = ", ".join(f"{n} {num(v)}%" for n, v in quiet)
    longq = [100 * float(np.mean(r.cells[("spin", 1000)]["gap_us"] < 0)) for r in data["B"][1] if r.has("spin", 1000)]
    sub = ("Intel i7-8700, bench, POLL policy; shadow every 50 µs. Line: median over runs (3 runs: "
           "seed 1 twice and seed 2; Δt = 1 s is a separate *-long run); dots: runs.\n"
           f"Trials with no interrupt during the pause (gap_us = −1) in B at Δt = 1–100 ms: {ql}"
           + (f"; at Δt = 1 s: {num(longq[0])}%." if longq else ".")
           + "\np99 from ~250 trials per run (~3 points beyond it): descriptive only.")
    fig, rect = frame(13.5, 6.3, title, sub, left=0.95, right=0.35, bottom=0.75)
    gs = fig.add_gridspec(1, 2, wspace=0.22, **rect)
    ax1, ax2 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    jit = {"A": 0.94, "B": 1.06}
    for ax, metric in ((ax1, "pen"), (ax2, "p99")):
        style(ax, grid="both")
        modes = ["spin", "spinshadow"] if metric == "pen" else ["spin"]
        for m in modes:
            for c, lab, fill in conds:
                short, long_ = data[c]
                rr = short + long_
                dts = sorted({k[1] for r in rr for k in r.cells if k[0] == m and k[1] > 0})
                xs, ys = [], []
                for dt in dts:
                    v = [(r.pen(m, dt) if metric == "pen" else q(r.ns(m, dt), .99)) for r in rr if r.has(m, dt)]
                    ax.plot([dt * jit[c]] * len(v), v, linestyle="none", marker=mk(m), markersize=5.5,
                            color=col(m), alpha=.45, markerfacecolor=col(m) if fill else SURF,
                            markeredgecolor=col(m), markeredgewidth=1.0, zorder=2)
                    xs.append(dt), ys.append(med(v))
                ax.plot(xs, ys, color=col(m), linestyle="-" if fill else "--", marker=mk(m), markersize=9,
                        markerfacecolor=col(m) if fill else SURF, markeredgecolor=SURF if fill else col(m),
                        markeredgewidth=1.5 if fill else 1.8, zorder=3)
                if metric == "pen" and m == "spin":
                    ax.annotate(f"{c}: {num(ys[-1])} ns",
                                (xs[-1], ys[-1]), xytext=(9, 0), textcoords="offset points", va="center",
                                fontsize=9, color=INK)
        log_axis(ax, "x")
        ticks = [1, 10, 100, 1000]
        ax.set_xticks(ticks, ["1", "10", "100", "1000"])
        ax.set_xlim(0.6, 2600)
        ax.set_xlabel("pause Δt, ms (log scale)")
    ax1.axhline(0, color=MUTED, linewidth=1.2, zorder=1)
    ax1.set_ylim(-10, None)
    ax1.set_ylabel("first-call penalty vs hot, ns")
    ax1.set_title("a) First-call penalty, p50")
    sp = [v for c in data for r in data[c][0] for dt in (1, 10, 100) if r.has("spinshadow", dt)
          for v in [r.pen("spinshadow", dt)]]
    if sp:
        ax1.annotate("spin + shadow every 50 µs", (10, max(sp)), xytext=(0, 8), textcoords="offset points",
                     ha="center", va="bottom", fontsize=9, color=INK)
    log_axis(ax2, "y")
    ax2.set_ylabel("first-call p99, ns (log scale)")
    ax2.set_title("b) Busy-wait first-call tail, p99: no smaller in B")
    ax2.yaxis.set_major_locator(ticker.FixedLocator([100, 200, 300, 500, 1000, 2000]))
    ax2.set_ylim(150, 2200)
    legend(fig, [key("spin", "busy-wait loop (spin)", line=False), key("spinshadow", "spin + shadow", line=False),
                 Line2D([], [], color=INK2, linestyle="-", marker="o", markersize=8, markeredgecolor=SURF,
                        label=conds[0][1]),
                 Line2D([], [], color=INK2, linestyle="--", marker="o", markersize=8, markerfacecolor=SURF,
                        markeredgewidth=1.6, label=conds[1][1] + " (hollow)")])
    return save(fig, name, a.out)



# ---------------------------------------------------------------- 13–18. handler-size axis (size.sh, via size.py)
SZ_POINTS = ["cpptrader", "tx", "c32", "c128", "d64", "big"]
SZ_LABEL = {"cpptrader": "S"}
SZ_DESC = {"cpptrader": "CppTrader", "tx": "S + sendto", "c32": "code 32 KB", "c128": "code 128 KB",
           "d64": "data 64 KB", "d64i": "data 64 KB,\nindependent", "big": "code 128 KB +\ndata + sendto"}
# Ordinal ramp of the spinshadow hue by code size (validate_palette.js --ordinal: pass), for one line per point.
SZ_RAMP = {"cpptrader": "#4ec492", "tx": "#29a777", "c32": "#008b5c", "c128": "#007043", "big": "#00562c"}
SZ_MARK = {"cpptrader": "o", "tx": "^", "c32": "D", "c128": "s", "big": "v"}  # second channel next to the ramp
BIG_NOTE = ("big shows the absolute first-call time, not a penalty: its “hot” is shifted (back-to-back sendto makes the hot "
            "call slower than the first call after a pause).")
_SIZE = {}


def size_api():
    """size.py as a library (its Run, runs, Missing), scanned once over ROOT; None if unavailable."""
    if "mod" not in _SIZE:
        try:
            import size as mod
            if not mod.DIRS:
                mod.scan(ROOT)
            _SIZE["mod"] = mod
        except Exception as e:  # noqa: BLE001
            print(f"  (cannot load size.py: {e})")
            _SIZE["mod"] = None
    return _SIZE["mod"]


def sz(regex, fn):
    """(median, min, max, n) of fn over the finished size runs matching regex; None if no value."""
    S = size_api()
    if S is None:
        return None
    vals = []
    for r in S.runs(regex):
        try:
            v = fn(r)
        except S.Missing:
            continue
        if v is not None and v == v:
            vals.append(v)
    return (med(vals), min(vals), max(vals), len(vals)) if vals else None


def sz_has(regex):
    S = size_api()
    return bool(S and S.runs(regex))


def lab(p, desc=True):
    return SZ_LABEL.get(p, p) + (f"\n{SZ_DESC[p]}" if desc else "")


def dot(ax, x, v, m, scale=1.0, color=None, fill=True, ms=9, zorder=3, alpha_bar=.6):
    """Median marker with a min–max bar; v = (med, lo, hi, n)."""
    c = color or col(m)
    ax.vlines(x, v[1] * scale, v[2] * scale, color=c, linewidth=2, alpha=alpha_bar, zorder=zorder - 1)
    ax.plot(x, v[0] * scale, marker=mk(m), markersize=ms, linestyle="none", color=c,
            markerfacecolor=c if fill else SURF, markeredgecolor=SURF if fill else c,
            markeredgewidth=1.4 if fill else 1.8, zorder=zorder)


def fig13(a):
    name = "fig13_size_spin.png"
    pts = [p for p in SZ_POINTS if sz_has(rf"szB-{p}-r\d+")]
    if not pts:
        return skip(name, "no runs of series B (*-szB-<point>-r*) or no size.py")
    modes = ["spin", "spinshadow", "spinpf"]
    small = [p for p in pts if p != "big"]
    pen = {(p, m): sz(rf"szB-{p}-r\d+", lambda r, m=m: 1000 * r.pen(m, .5)) for p in small for m in modes}
    rem = {(p, m): sz(rf"szB-{p}-r\d+", lambda r, m=m: r.removed("spin", m, .5)) for p in small for m in modes[1:]}
    cost = {(p, m): sz(rf"szB-{p}-r\d+", lambda r, m=m: r.cost(m)) for p in pts for m in modes[1:]}
    nr = {p: len(size_api().runs(rf"szB-{p}-r\d+")) for p in pts}
    # title from the data: code-size points, cost range, prefetch on 128 KB of code
    code = [rem[(p, "spinshadow")][0] for p in ("cpptrader", "c32", "c128") if rem.get((p, "spinshadow"))]
    cs = [cost[(p, "spinshadow")][0] for p in pts if cost.get((p, "spinshadow"))]
    title = "Busy-wait loop: shadow and prefetch by handler size"
    if code and cs:
        title = (f"Shadow in the spin loop removes {num(min(code))}–{num(max(code))}% of the penalty at any code size, "
                 f"but costs {num(cs[0], 2)}→{num(cs[-1])}% of the core")
        pf = rem.get(("c128", "spinpf")), cost.get(("c128", "spinpf"))
        if all(pf):
            title += f"; for 128 KB of code, prefetch removes {num(pf[0][0])}% for {num(pf[1][0], 1)}%"
    tx = rem.get(("tx", "spinshadow"))
    sub = ("Intel i7-8700, POLL policy, Δt = 10 ms, period T = 50 µs; "
           f"{max(nr.values())} runs of 1000 trials per point. Dot: median of p50 over runs; bar: spread "
           "between runs; label: share of the spin penalty removed by the warm-up.\n"
           + (f"For tx, shadow removes {num(tx[0])}% (send warm-up: Fig. 16). " if tx else "")
           + "Penalties < 1 ns are clamped to the bottom of the axis; the removed share is shown only if the spin penalty is > 50 ns in all runs.\n"
           + BIG_NOTE)
    fig, rect = frame(16.5, 6.6, title, sub, left=0.85, right=0.3, bottom=1.05)
    gs = fig.add_gridspec(1, 3, width_ratios=[len(small) + 0.4, 2.7, len(pts) + 0.4], wspace=0.28, **rect)
    ax1, ax2, ax3 = (fig.add_subplot(gs[i]) for i in range(3))
    off = dict(zip(modes, (-0.22, 0, 0.22)))
    style(ax1, grid="y")
    for i, p in enumerate(small):
        for m in modes:
            v = pen.get((p, m))
            if not v:
                continue
            v = tuple(max(x, 1.0) if k < 3 else x for k, x in enumerate(v))
            dot(ax1, i + off[m], v, m)
            rv = rem.get((p, m))
            if rv and rv[3] == nr[p] and (m == "spinshadow" or (m == "spinpf" and rv[0] >= 50)):
                ax1.annotate(f"−{num(rv[0])}%", (i + off[m], v[1]), xytext=(0, -5), textcoords="offset points",
                             ha="center", va="top", fontsize=8.3, color=INK)
    log_axis(ax1, "y")
    ax1.set_ylim(0.6, 2000)
    ax1.set_xticks(range(len(small)), [lab(p) for p in small], fontsize=8.6)
    ax1.set_xlim(-0.6, len(small) - 0.4)
    ax1.tick_params(axis="x", length=0)
    ax1.set_ylabel("first-call penalty vs hot, ns (log)")
    ax1.set_title("a) Penalty: spin and what remains after warm-up")
    style(ax2, grid="y")
    if "big" in pts:
        cells = ["hot", "spin", "spinshadow", "spinpf"]
        for i, m in enumerate(cells):
            v = sz(r"szB-big-r\d+", lambda r, m=m: r.q(m, .5))
            if v:
                dot(ax2, i, v, m)
                ax2.annotate(num(v[0], 1), (i, v[2]), xytext=(0, 5), textcoords="offset points", ha="center",
                             va="bottom", fontsize=8.3, color=INK)
        ax2.set_xticks(range(len(cells)), ["hot*", "spin", "+ shadow", "+ pre-\nfetch"], fontsize=8.6)
        ax2.set_xlim(-0.6, len(cells) - 0.4)
        lo, hi = ax2.get_ylim()
        ax2.set_ylim(lo - 0.08 * (hi - lo), hi + 0.15 * (hi - lo))
    ax2.tick_params(axis="x", length=0)
    ax2.set_ylabel("first call, µs")
    ax2.set_title("b) big: first call, p50")
    style(ax3, grid="y")
    for i, p in enumerate(pts):
        for m, o in (("spinshadow", -0.13), ("spinpf", 0.13)):
            v = cost.get((p, m))
            if v:
                dot(ax3, i + o, v, m)
                if (m == "spinshadow" and p in (pts[0], pts[-1])) or (m == "spinpf" and p == "c128"):
                    ax3.annotate(f"{num(v[0], 2 if v[0] < 1 else 1 if v[0] < 10 else 0)}%", (i + o, v[0]),
                                 xytext=(8, 0), textcoords="offset points", va="center", fontsize=8.3, color=INK)
    log_axis(ax3, "y")
    ax3.set_ylim(0.1, 100)
    ax3.set_xticks(range(len(pts)), [lab(p) for p in pts], fontsize=8.6)
    ax3.set_xlim(-0.6, len(pts) - 0.4)
    ax3.tick_params(axis="x", length=0)
    ax3.set_ylabel("core share spent on warm-ups, % (log)")
    ax3.set_title("c) Warm-up cost at T = 50 µs")
    legend(fig, [key("spin", "spin, no warm-up", line=False), key("spinshadow", "spin + shadow", line=False),
                 key("spinpf", "spin + prefetch", line=False), key("hot", "hot (* shifted for big)", line=False)])
    return save(fig, name, a.out)


def fig14(a):
    name = "fig14_size_period.png"
    pts = [p for p in ("tx", "c32", "c128", "big") if sz_has(rf"szT-{p}-T\d+")]
    if not pts:
        return skip(name, "no runs of series T (*-szT-<point>-T*)")
    S = size_api()

    def cells(p):
        ts = sorted({int(n.rsplit("-T", 1)[1]) for n in S.DIRS if re.fullmatch(rf"szT-{p}-T\d+", n)})
        out = [(50, rf"szB-{p}-r\d+")] if sz_has(rf"szB-{p}-r\d+") else []
        return out + [(t, rf"szT-{p}-T{t}") for t in ts]

    fig, rect = frame(16, 6.2, "Shadow period in the spin loop by handler size",
                      "Intel i7-8700, POLL policy, Δt = 10 ms. T = 50 µs: series B (3 runs); other T: "
                      "series T (1 run, 1000 trials). Dot: median of p50 over runs; bar: spread.\n"
                      "Colour: point on the size axis (lighter = less code). S: series f0-ss* (13–14 Nov, 300 trials). "
                      + BIG_NOTE, left=0.85, right=0.3, bottom=0.8)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.25, 0.9, 1.1], wspace=0.28, **rect)
    ax1, ax2, ax3 = (fig.add_subplot(gs[i]) for i in range(3))
    for ax in (ax1, ax2, ax3):
        style(ax, grid="both")
    handles, ends1, ends3, summary = [], [], [], {}
    # S from the earlier f0-ss* sweep (same policy and Δt), for reference
    s_pts = []
    for r in runs("f0-ss*"):
        m = re.fullmatch(r"f0-ss(\d+)", r.name)
        if m and r.has("spinshadow", 10):
            t = int(m.group(1))
            ex = r.excess("spinshadow", 10)
            s_pts.append((t, max(q(ex, .5), 1.0), 100 * np.mean(r.cells[("spinshadow", 10)]["warm_us"]) / 10000))
    if s_pts:
        s_pts.sort()
        c = SZ_RAMP["cpptrader"]
        ax1.plot([t for t, *_ in s_pts], [v for _, v, _ in s_pts], color=c, marker=SZ_MARK["cpptrader"],
                 markersize=7, markeredgecolor=SURF, markeredgewidth=1.2)
        ax3.plot([t for t, *_ in s_pts], [v for *_, v in s_pts], color=c, marker=SZ_MARK["cpptrader"],
                 markersize=7, markeredgecolor=SURF, markeredgewidth=1.2)
        ends1.append((s_pts[-1][0], s_pts[-1][1], "S"))
        ends3.append((s_pts[-1][0], s_pts[-1][2], "S"))
        handles.append(Line2D([], [], color=c, marker=SZ_MARK["cpptrader"], markersize=8, markeredgecolor=SURF,
                              label="S (f0-ss*)"))
    for p in pts:
        c = SZ_RAMP[p]
        xs, ys, cs = [], [], []
        for t, rx in cells(p):
            v = sz(rx, (lambda r: r.q("spinshadow", .5)) if p == "big" else (lambda r: 1000 * r.pen("spinshadow", .5)))
            cv = sz(rx, lambda r: r.cost("spinshadow"))
            if v:
                xs.append(t), ys.append(v)
            if cv:
                cs.append((t, cv))
        if p != "big" and xs:
            yy = [tuple(max(x, 1.0) for x in v[:3]) for v in ys]
            ax1.vlines(xs, [v[1] for v in yy], [v[2] for v in yy], color=c, linewidth=2, alpha=.6)
            ax1.plot(xs, [v[0] for v in yy], color=c, marker=SZ_MARK[p], markersize=8, markeredgecolor=SURF,
                     markeredgewidth=1.3)
            ends1.append((xs[-1], yy[-1][0], SZ_LABEL.get(p, p)))
            summary[p] = (xs, [v[0] for v in ys])
        if p == "big" and xs:
            for m, fn in (("hot", lambda r: r.q("hot", .5)), ("spin", lambda r: r.q("spin", .5))):
                ref = [(t, sz(rx, fn)) for t, rx in cells(p)]
                ref = [(t, v) for t, v in ref if v]
                if ref:
                    ax2.plot([t for t, _ in ref], [v[0] for _, v in ref], color=col(m), marker=mk(m), markersize=7,
                             markeredgecolor=SURF, markeredgewidth=1.2, linewidth=1.6 if m == "hot" else 2)
                    ax2.annotate("hot*" if m == "hot" else "spin", (ref[-1][0], ref[-1][1][0]), xytext=(7, 0),
                                 textcoords="offset points", va="center", fontsize=9, color=INK)
            ax2.vlines(xs, [v[1] for v in ys], [v[2] for v in ys], color=c, linewidth=2, alpha=.6)
            ax2.plot(xs, [v[0] for v in ys], color=c, marker=SZ_MARK[p], markersize=8, markeredgecolor=SURF,
                     markeredgewidth=1.3)
            ax2.annotate("shadow", (xs[-1], ys[-1][0]), xytext=(7, 0), textcoords="offset points", va="center",
                         fontsize=9, color=INK)
        if cs:
            ax3.vlines([t for t, _ in cs], [v[1] for _, v in cs], [v[2] for _, v in cs], color=c, linewidth=2,
                       alpha=.6)
            ax3.plot([t for t, _ in cs], [v[0] for _, v in cs], color=c, marker=SZ_MARK[p], markersize=8,
                     markeredgecolor=SURF, markeredgewidth=1.3)
            ends3.append((cs[-1][0], cs[-1][1][0], SZ_LABEL.get(p, p)))
        handles.append(Line2D([], [], color=c, marker=SZ_MARK[p], markersize=8, markeredgecolor=SURF,
                              label=f"{SZ_LABEL.get(p, p)}: {SZ_DESC[p]}".replace("\n", " ")))
    for ax in (ax1, ax2, ax3):
        log_axis(ax, "x")
        ax.set_xticks([10, 20, 50, 100, 200, 500, 1000], ["10", "20", "50", "100", "200", "500", "1000"])
        ax.set_xlim(8, 2500)
        ax.set_xlabel("shadow period T, µs (log)")
    log_axis(ax1, "y")
    ax1.set_ylim(0.8, 1000)
    ax1.set_ylabel("remaining spinshadow penalty, ns (log)")
    ax1.set_title("a) Remaining penalty, p50")
    ax2.set_ylabel("first call, µs")
    ax2.set_title("b) big: first call, p50")
    log_axis(ax3, "y")
    ax3.set_ylim(0.01, 100)
    ax3.set_ylabel("core share spent on warm-ups, % (log)")
    ax3.set_title("c) Shadow cost")
    end_labels(ax1, ends1)
    end_labels(ax3, ends3)
    # title from the data: remainder and cost at the shortest and the longest T common to the non-big points
    def at_t(t):
        rem_ = [sz(rx, lambda r: 1000 * r.pen("spinshadow", .5)) for p in pts if p != "big" for tt, rx in cells(p)
                if tt == t]
        cost_ = [sz(rx, lambda r: r.cost("spinshadow")) for p in pts for tt, rx in cells(p) if tt == t]
        rem_, cost_ = [v[0] for v in rem_ if v], [v[0] for v in cost_ if v]
        return (min(rem_), max(rem_), min(cost_), max(cost_)) if rem_ and cost_ else None
    common = sorted(set.intersection(*[{t for t, _ in cells(p)} for p in pts if p != "big"])) if any(
        p != "big" for p in pts) else []
    if len(common) >= 2:
        lo_t, hi_t = at_t(common[0]), at_t(common[-1])
        if lo_t and hi_t:
            fmt = lambda v: num(v, 2 if v < 1 else 1 if v < 10 else 0)  # noqa: E731
            fig.texts[0].set_text(
                f"Shadow period: at T = {common[0]} µs the remainder is {num(lo_t[0])}–{num(lo_t[1])} ns for {fmt(lo_t[2])}–"
                f"{fmt(lo_t[3])}% of the core; at T = {common[-1]} µs, {num(hi_t[0])}–{num(hi_t[1])} ns for "
                f"{fmt(hi_t[2])}–{fmt(hi_t[3])}%")
    legend(fig, handles, ncol=min(len(handles), 6))
    return save(fig, name, a.out)


def fig15(a):
    name = "fig15_size_sleep.png"
    pts = [p for p in SZ_POINTS if sz_has(rf"szA-{p}-r\d+")]
    if not pts:
        return skip(name, "no runs of series A (*-szA-<point>-r*)")
    rx = lambda p: rf"szA-{p}-r\d+"  # noqa: E731
    hot = {p: sz(rx(p), lambda r: r.q("hot", .5)) for p in pts}
    none = {p: sz(rx(p), lambda r: r.q("none", .5)) for p in pts}
    pen = {p: sz(rx(p), lambda r: r.pen("none", .5)) for p in pts if p != "big"}
    nA = {p: len(size_api().runs(rx(p))) for p in pts}
    tl = lambda p: f"{lab(p)}\n{nA[p]} runs"  # noqa: E731
    warm = ["shadow", "prewake", "pfwake"]
    rem = {(p, m): sz(rx(p), lambda r, m=m: r.removed("none", m, .5)) for p in pts if p != "big" for m in warm}
    pw = [v[0] for (p, m), v in rem.items() if m == "prewake" and v]
    ps = [v[0] for p, v in pen.items() if v]
    title = "Sleep (control): penalty after sleep and what warm-up removes, by handler size"
    if pw and ps:
        title = (f"Sleep (control): penalty after sleep {num(min(ps), 1)}–{num(max(ps), 1)} µs; "
                 f"prewake removes {num(min(pw))}–{num(max(pw))}%")
    fig, rect = frame(15.5, 6.4, title,
                      "Intel i7-8700, default policy (C6+), Δt = 10 ms; shadow through sleep every 1 ms; prewake and "
                      "pfwake (prefetch dc): wake 200 µs early (400 µs for c128 and big).\nDot: median of p50 over "
                      "runs (1000 trials each); bar: spread. Removed share = (none − X) / (none − hot), only when the "
                      "none penalty > 50 ns.\n" + BIG_NOTE.replace("shows the absolute", "shows only the absolute"),
                      left=0.85, right=0.3, bottom=1.05)
    gs = fig.add_gridspec(1, 2, width_ratios=[len(pts), len(pts) - ("big" in pts) + 0.6], wspace=0.22, **rect)
    ax1, ax2 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    style(ax1, grid="y")
    for i, p in enumerate(pts):
        if hot[p] and none[p]:
            ax1.plot([i, i], [hot[p][0], none[p][0]], color=AXIS, linewidth=1.5, zorder=1)
        if hot[p]:
            dot(ax1, i, hot[p], "hot", ms=8)
        if none[p]:
            dot(ax1, i, none[p], "none")
            if p in pen and pen[p]:
                ax1.annotate(f"+{num(pen[p][0], 1)}", (i, none[p][2]), xytext=(0, 5), textcoords="offset points",
                             ha="center", va="bottom", fontsize=8.3, color=INK)
    log_axis(ax1, "y")
    ax1.set_xticks(range(len(pts)), [tl(p) for p in pts], fontsize=8.6)
    ax1.set_xlim(-0.6, len(pts) - 0.4)
    ax1.tick_params(axis="x", length=0)
    ax1.set_ylabel("first call, µs (log)")
    ax1.set_title("a) Hot and first call after sleep; label: penalty, µs")
    style(ax2, grid="y")
    small = [p for p in pts if p != "big"]
    off = dict(zip(warm, (-0.22, 0, 0.22)))
    for i, p in enumerate(small):
        for m in warm:
            v = rem.get((p, m))
            if v:
                dot(ax2, i + off[m], v, m)
    ax2.axhline(0, color=AXIS, linewidth=1)
    ax2.axhline(100, color=MUTED, linewidth=1)
    ax2.text(len(small) - 0.45, 101.5, "100% = as hot", ha="right", va="bottom", fontsize=8.5, color=INK2)
    ax2.set_ylim(-20, 120)
    ax2.set_xticks(range(len(small)), [tl(p) for p in small], fontsize=8.6)
    ax2.set_xlim(-0.6, len(small) - 0.4)
    ax2.tick_params(axis="x", length=0)
    ax2.set_ylabel("removed from the sleep penalty, %")
    ax2.set_title("b) Share of the penalty removed by warm-up")
    legend(fig, [key("hot", "hot", line=False), key("none", "sleep, no warm-up", line=False),
                 key("shadow", "sleep + shadow (1 ms)", line=False), key("prewake", "prewake", line=False),
                 key("pfwake", "pfwake (prefetch dc)", line=False)])
    return save(fig, name, a.out)


def fig16(a):
    name = "fig16_size_send.png"
    pts = [p for p in ("tx", "big") if sz_has(rf"szW-{p}-w\d-(spin|sleep)")]
    if not pts:
        return skip(name, "no runs of series W (*-szW-<point>-w<0|1|2>-spin|sleep)")
    ws = [0, 1, 2]
    wlab = ["W0\nno send", "W1\nencoding", "W2\nenc. + sendto"]
    parts = [("spin", ["spin", "spinshadow"], "spin (POLL), shadow every 1 ms"),
             ("sleep", ["none", "prewake"], "sleep (C6+), prewake 400 µs early")]
    get = lambda p, w, part, m, qq=.5: sz(rf"szW-{p}-w{w}-{part}", lambda r: r.q(m, qq))  # noqa: E731
    title = "Send warm-up: what encoding the order (W1) and a real sendto (W2) add to the warm-up"
    a0, a2 = get("tx", 0, "spin", "spinshadow"), get("tx", 2, "spin", "spinshadow")
    b0, b2 = get("big", 0, "sleep", "prewake"), get("big", 2, "sleep", "prewake")
    if a0 and a2:
        title = f"Send warm-up: for tx, spin + shadow {num(a0[0], 2)} → {num(a2[0], 2)} µs from W0 to W2"
        if b0 and b2:
            title += f"; for big, prewake {num(b0[0], 1)} → {num(b2[0], 1)} µs"
    fig, rect = frame(14, 8.4, title,
                      "Intel i7-8700, Δt = 10 ms, 1 run per cell (1000 trials). Absolute first-call time: "
                      "dot: p50, bar: p10–p90 of trials; grey line: hot call (shifted up for big: "
                      "back-to-back sendto).\nW0: warm-up without encoding or sending; W1: order encoding without sendto; "
                      "W2: encoding and sendto to a second socket. Each panel has its own scale.", left=0.95, bottom=1.0)
    gs = fig.add_gridspec(len(pts), 2, wspace=0.22, hspace=0.55, **rect)
    for i, p in enumerate(pts):
        for j, (part, modes, ptitle) in enumerate(parts):
            ax = fig.add_subplot(gs[i, j])
            style(ax, grid="y")
            hv = [get(p, w, part, "hot") for w in ws]
            hv = [v[0] for v in hv if v]
            if hv:
                ax.axhline(med(hv), color=MUTED, linewidth=1.5, zorder=1)
                ax.annotate("hot" + ("*" if p == "big" else ""), (-0.55, med(hv)), xytext=(0, 3),
                            textcoords="offset points", ha="left", va="bottom", fontsize=8.5, color=INK2)
            for k, m in enumerate(modes):
                for w in ws:
                    v = get(p, w, part, m)
                    if not v:
                        continue
                    lo, hi = get(p, w, part, m, .1), get(p, w, part, m, .9)
                    dot(ax, w + (k - 0.5) * 0.3, (v[0], lo[0], hi[0], 1), m)
            ax.set_xticks(ws, wlab, fontsize=8.6)
            ax.set_xlim(-0.6, 2.6)
            ax.tick_params(axis="x", length=0)
            ax.set_title(f"{'abcd'[2 * i + j]}) {SZ_LABEL.get(p, p)} ({SZ_DESC[p]}): {ptitle}".replace("\n", " "),
                         fontsize=10)
            ax.set_ylabel("first call, µs")
    legend(fig, [key("spin", "spin, no warm-up", line=False), key("spinshadow", "spin + shadow", line=False),
                 key("none", "sleep, no warm-up", line=False), key("prewake", "prewake", line=False),
                 key("hot", "hot (* shifted for big)", line=False)])
    return save(fig, name, a.out)


def fig17(a):
    name = "fig17_size_tails.png"
    pts = [p for p in ("cpptrader", "big") if sz_has(rf"szP-{p}-(spin|phase|sleep)")]
    if not pts:
        return skip(name, "no runs of series P (*-szP-<point>-spin|phase|sleep)")
    S = size_api()
    cells = [("spin", "hot", "hot (spin run)", dict(color=col("hot"), marker="o", ls="-", fill=True)),
             ("spin", "spin", "busy-wait (spin)", dict(color=col("spin"), marker=mk("spin"), ls="-", fill=True)),
             ("spin", "spinshadow", "spin + shadow, grid-aligned phase", dict(color=col("spinshadow"), marker="s", ls="-",
                                                                      fill=True)),
             ("phase", "spinshadow", "spin + shadow, random phase", dict(color=col("spinshadow"), marker="s",
                                                                        ls="--", fill=False)),
             ("sleep", "none", "sleep, no warm-up", dict(color=col("none"), marker=mk("none"), ls="-", fill=True)),
             ("sleep", "prewake", "prewake", dict(color=col("prewake"), marker=mk("prewake"), ls="-", fill=True))]
    qs = [(.5, "p50"), (.99, "p99"), (.999, "p99.9")]
    tail, used = {}, []
    fig, rect = frame(max(10.5, 6.5 * len(pts) + 1.5), 6.6, "First-call tails over 20k trials: S and big",
                      "Intel i7-8700; spin: POLL, Δt = 1 ms, T = 50 µs; sleep: C6+, Δt = 10 ms. One run per cell, "
                      "~20k trials (~20 points beyond p99.9).\nAbsolute first-call time. Random phase: "
                      "the event lands at a random moment of the shadow period (it may coincide with a warm-up).\n" + BIG_NOTE,
                      left=0.95, bottom=0.8)
    gs = fig.add_gridspec(1, len(pts), wspace=0.22, **rect)
    for i, p in enumerate(pts):
        ax = fig.add_subplot(gs[i])
        style(ax, grid="both")
        for part, m, labl, st in cells:
            r = S.run(f"szP-{p}-{part}")
            if r is None:
                continue
            try:
                ys = [r.q(m, qq) for qq, _ in qs]
            except S.Missing:
                continue
            tail[(p, part, m)] = ys[-1]
            if labl not in used:
                used.append(labl)
            ax.plot(range(len(qs)), ys, color=st["color"], linestyle=st["ls"], marker=st["marker"], markersize=8,
                    markerfacecolor=st["color"] if st["fill"] else SURF,
                    markeredgecolor=SURF if st["fill"] else st["color"], markeredgewidth=1.4 if st["fill"] else 1.8,
                    linewidth=1.6 if m == "hot" else 2)
        log_axis(ax, "y")
        nice_log_ticks(ax, "y")
        ax.set_xticks(range(len(qs)), [n for _, n in qs])
        ax.set_xlim(-0.3, len(qs) - 0.7)
        ax.set_xlabel("first-call quantile")
        ax.set_ylabel("first call, µs (log)")
        ax.set_title(f"{'ab'[i]}) {SZ_LABEL.get(p, p)}: {SZ_DESC[p]}".replace("\n", " "))
    sp = [(p, tail.get((p, "spin", "spinshadow")), tail.get((p, "spin", "spin"))) for p in pts]
    sp = [(p, a_, b_) for p, a_, b_ in sp if a_ and b_]
    if sp:
        fig.texts[0].set_text("Tails over 20k trials, first-call p99.9 in the spin loop: " + "; ".join(
            f"{SZ_LABEL.get(p, p)}: shadow {num(a_, 2 if a_ < 10 else 1)} vs spin {num(b_, 2 if b_ < 10 else 1)} µs"
            for p, a_, b_ in sp))
    handles = [Line2D([], [], color=st["color"], linestyle=st["ls"], marker=st["marker"], markersize=8,
                      markerfacecolor=st["color"] if st["fill"] else SURF,
                      markeredgecolor=SURF if st["fill"] else st["color"], markeredgewidth=1.4, label=labl)
               for _, _, labl, st in cells if labl in used]
    legend(fig, handles, ncol=min(6, len(handles)) if len(handles) <= 6 else 3)
    return save(fig, name, a.out)


def fig18(a):
    name = "fig18_size_prefetch_c3.png"
    groups = [(p, s) for p, s in (("d64", "d"), ("d64i", "d"), ("c128", "c"), ("c128", "dc"))
              if sz_has(rf"szF-{p}-{s}-r\d+")]
    if not groups:
        return skip(name, "no runs of series F (*-szF-<point>-<set>-r*)")
    modes = ["none", "prewake", "pfwake", "pfevent"]
    setname = {"d": "data", "c": "code", "dc": "code+data"}
    pen = {(g, m): sz(rf"szF-{g[0]}-{g[1]}-r\d+", lambda r, m=m: r.pen(m, .5)) for g in groups for m in modes}
    rem = {g: sz(rf"szF-{g[0]}-{g[1]}-r\d+", lambda r: r.removed("none", "pfwake", .5)) for g in groups}
    title = "Prefetch after C3 by handler size"
    parts = [f"{SZ_LABEL.get(p, p)} ({setname[s]}) {num(rem[(p, s)][0])}%" for p, s in groups if rem.get((p, s))]
    if parts:
        title = "Prefetch before the event after C3 removes: " + ", ".join(parts)
    fig, rect = frame(14, 6.4, title,
                      "Intel i7-8700, policy up to C3, Δt = 10 ms; 2 runs of 1000 trials per set. Bar: median of "
                      "the p50 penalty over runs; whisker: spread.\npfwake and prewake: wake 200 µs early (c128: "
                      "400 µs), prefetch 20 µs before the event; pfevent: prefetch at the event.\nd64: 1024 dependent "
                      "reads (a chain); d64i: the same lines, independent reads. Numbers in the title: share of the "
                      "sleep penalty removed by pfwake.", left=0.95, bottom=1.05, panel_titles=False)
    ax = fig.add_subplot(fig.add_gridspec(1, 1, **rect)[0])
    style(ax, grid="y")
    w = 0.19
    for i, g in enumerate(groups):
        for k, m in enumerate(modes):
            v = pen.get((g, m))
            if not v:
                continue
            x = i + (k - 1.5) * (w + 0.02)
            ax.bar(x, v[0], width=w, color=col(m), linewidth=0, zorder=2)
            ax.vlines(x, v[1], v[2], color=INK2, linewidth=1.2, zorder=3)
            ax.annotate(num(v[0], 2 if abs(v[0]) < 1 else 1), (x, max(v[2], v[0])), xytext=(0, 3),
                        textcoords="offset points", ha="center", va="bottom", fontsize=8.3, color=INK)
    ax.axhline(0, color=AXIS, linewidth=1)
    ax.set_xticks(range(len(groups)), [f"{SZ_LABEL.get(p, p)}: {SZ_DESC[p]}\nset “{setname[s]}”".replace(",\n", ", ")
                                       for p, s in groups], fontsize=9)
    ax.tick_params(axis="x", length=0)
    ax.set_xlim(-0.6, len(groups) - 0.4)
    ax.set_ylabel("first-call penalty vs hot, µs")
    legend(fig, [key(m, label, line=False, marker="s", markersize=10) for m, label in
                 (("none", "sleep, no warm-up"), ("prewake", "prewake"), ("pfwake", "pfwake: prefetch 20 µs early"),
                  ("pfevent", "pfevent: prefetch at the event"))])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 19. targeted disruption (disrupt.py summary)
def parse_dis(path):
    """Section A: name → dict(p50, lo, hi, p99, net, net_lo, net_hi, ctrl); D: additivity rows and synthetic C3/C6."""
    rng = r"(-?\d+) \((-?\d+)…(-?\d+)\)"
    out = {"A": {}, "D": {}, "synth": {}}
    sec = None
    with open(path, errors="replace") as f:
        for line in f:
            m = re.match(r"^([A-G])\. ", line)
            if m:
                sec = m.group(1)
                continue
            if sec == "A":
                m = re.match(rf"^\s+(\S+)\s+{rng}\s*{rng}\s+(.*)$", line)
                if m and m.group(1) not in ("disruptor", "base"):
                    d = dict(p50=int(m.group(2)), lo=int(m.group(3)), hi=int(m.group(4)), p99=int(m.group(5)))
                    n = re.search(rf"{rng} \(([^)]+)\)", m.group(8))
                    if n:
                        d.update(net=int(n.group(1)), net_lo=int(n.group(2)), net_hi=int(n.group(3)), ctrl=n.group(4))
                    out["A"][m.group(1)] = d
                m = re.match(rf"^\s+(\S+) \(alone, (\d+) trials\): p50 {rng}", line)
                if m:
                    out["A"][m.group(1)] = dict(p50=int(m.group(3)), lo=int(m.group(4)), hi=int(m.group(5)),
                                                note=f"separate run, {m.group(2)} trials")
            if sec == "D":
                m = re.match(rf"^\s+(\S+\+\S+)\s+{rng}\s+{rng}", line)
                if m:
                    out["D"][m.group(1)] = dict(extra=int(m.group(2)), sum=int(m.group(5)), sum_lo=int(m.group(6)),
                                                sum_hi=int(m.group(7)))
                m = re.match(r"^\s+synth\. (C\d) = (\S+): (-?\d+) ns vs real (-?\d+) \(([+−-]?\d+)%", line)
                if m:
                    out["synth"][m.group(1)] = dict(parts=m.group(2).split("+"), joint=int(m.group(3)),
                                                    real=int(m.group(4)), pct=int(m.group(5).replace("−", "-")))
    return out


def fig19(a):
    name = "fig19_decompose.png"
    path = os.path.join(ROOT, "dis-summary.txt")
    if not os.path.exists(path):
        return skip(name, f"no {path} (disrupt.py)")
    D = parse_dis(path)
    A = D["A"]
    if len(A) < 5:
        return skip(name, "section A of dis-summary.txt could not be parsed")
    rows = [  # (family, label, key, use net of control)
        ("Data caches", "L1d: 64 KB of unrelated data", "d1:64k", False),
        ("Data caches", "L2: 1 MB of unrelated data", "d2:1m", False),
        ("Data caches", "L3: 64 MB of unrelated data¹", "d3:64m", False),
        ("Data caches", "handler data lines → memory (clflush)", "clf:d", False),
        ("TLB (net of control)", "data TLB: 3072 pages", "dtlb:3072", True),
        ("TLB (net of control)", "code TLB: 3072 pages", "itlb:3072", True),
        ("TLB (net of control)", "whole TLB: flush via mprotect", "tlbflush", True),
        ("Code", "L1i + micro-op cache: 64 KB of code", "line:64k", False),
        ("Code", "L2 for code: 1 MB of code", "line:1m", False),
        ("Code", "handler code lines → memory (clflush)", "clf:c", False),
        ("Branch prediction", "BTB: 128 KB of code with branches²", "jumpy:128k", True),
        ("Branch prediction", "direction: random branches, 16 sites", "dirn:16k", False),
        ("Branch prediction", "direction: 64 KB of branches", "dirw:64k", False),
        ("Branch prediction", "indirect: IBPB²", "ibpb", True),
        ("Controls", "OS kernel entry (getppid)", "sys", False),
        ("Controls", "MSR write", "wr0", False),
        ("Controls", "mprotect of one page", "mprot1", False),
    ]
    rows = [r for r in rows if r[2] in A and (not r[3] or "net" in A[r[2]])]
    synth = D["synth"]
    title = "Disrupting single structures before the call: what each one costs"
    if synth:
        pcts = [abs(v["pct"]) for v in synth.values()]
        lim = "±15%" if max(pcts) <= 15 else f"±{max(pcts)}%"
        title = (f"Synthetic {' and '.join(sorted(synth))} built from single disruptions are within {lim} of the real ones; "
                 "TLB, BTB and L2/L3 cost the most")
    fig, rect = frame(18, 8.8, title,
                      "Intel i7-8700, tickless core (nohz_full), POLL. Disruption: unrelated work right before the event "
                      "(Δt = 0); penalty: first call minus the empty disruption of the same run.\nBar: median of p50 over 4 "
                      "runs of 5k trials; whisker: range between them. TLB, BTB and IBPB are net of their "
                      "control (shown in the label). The structures do not separate cleanly.",
                      left=3.6, right=0.6, bottom=0.95)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.55, 1], wspace=0.62, **rect)
    ax1, ax2 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    # a) horizontal bars, grouped by family with a header row each
    BAR, CTRL = "#6f6e6a", "#b0aea7"
    y, ticks, labels, heads = 0, [], [], []
    for fam in dict.fromkeys(r[0] for r in rows):
        heads.append(y)
        ticks.append(y), labels.append(fam)
        y += 1
        for f_, lab_, k, net in rows:
            if f_ != fam:
                continue
            d = A[k]
            v, lo, hi = (d["net"], d["net_lo"], d["net_hi"]) if net else (d["p50"], d["lo"], d["hi"])
            c = CTRL if fam == "Controls" else BAR
            ax1.barh(y, v, height=0.62, color=c, linewidth=0, zorder=2)
            ax1.hlines(y, lo, hi, color=INK, linewidth=1.2, zorder=3)
            ax1.annotate(num(v), (max(v, hi), y), xytext=(5, 0), textcoords="offset points", va="center",
                         fontsize=8.5, color=INK)
            ticks.append(y), labels.append(lab_ + (f"  (− {d['ctrl']})" if net else ""))
            y += 1
        y += 0.35
    style(ax1, grid="x")
    ax1.axvline(0, color=AXIS, linewidth=1)
    ax1.set_yticks(ticks, labels)
    for t, lab_ in zip(ax1.get_yticklabels(), labels):
        t.set_color(INK)
        if lab_ in {r[0] for r in rows}:
            t.set_fontweight("bold")
            t.set_color(INK2)
    ax1.tick_params(axis="y", length=0, labelsize=9.2)
    ax1.set_ylim(y - 0.2, -0.8)
    ax1.set_xlim(-30, max(A[r[2]].get("hi", 0) for r in rows) * 1.12)
    ax1.set_xlabel("first-call penalty vs the empty disruption, ns (p50)")
    ax1.set_title("a) Single disruptions")
    notes = []
    if "d3:64m" in A and A["d3:64m"].get("note"):
        notes.append(f"¹ {A['d3:64m']['note']}")
    if any(r[2] in ("jumpy:128k", "ibpb") for r in rows):
        notes.append("² BTB: vs the same code without branches (line:128k); IBPB: vs an MSR write (wr0)")
    if notes:
        ax1.text(0, -0.09, "; ".join(notes), transform=ax1.transAxes, fontsize=8.3, color=INK2, va="top")
    # b) synthetic C3 / C6: parts (singles of section A, stacked), joint, real — horizontal like panel a
    style(ax2, grid="x")
    shades = ["#e6e4dd", "#d3d1c9"]
    short = {"line:1m": "code\n1 MB", "d2:1m": "data\n1 MB", "tlbflush": "TLB\nflush", "ibpb": "IBPB",
             "jumpy:256k": "BTB\n256 KB", "dirw:64k": "direction"}
    y, ticks, labels, heads = 0, [], [], []
    for cname in sorted(synth):
        s_ = synth[cname]
        ticks.append(y), labels.append(f"synthetic {cname}")
        heads.append(len(labels) - 1)
        y += 1
        left = 0
        for i, part in enumerate(s_["parts"]):
            v = A.get(part, {}).get("p50")
            if v is None:
                continue
            ax2.barh(y, v, left=left, height=0.7, color=shades[i % 2], edgecolor=SURF, linewidth=2, zorder=2)
            if v >= 150:
                ax2.text(left + v / 2, y, f"{short.get(part, part)}\n{num(v)}", ha="center", va="center",
                         fontsize=7.6, color=INK, linespacing=1.1)
            left += v
        ax2.annotate(f"Σ {num(left)}", (left, y), xytext=(5, 0), textcoords="offset points", va="center",
                     fontsize=8.5, color=INK)
        add = D["D"].get("+".join(s_["parts"]))
        if add:
            ax2.plot(add["sum"], y - 0.47, marker="v", markersize=8, color=INK, zorder=4, clip_on=False)
            ax2.annotate(f"Σ of these parts in the joint runs: {num(add['sum'])}", (add["sum"], y - 0.47),
                         xytext=(0, 6), textcoords="offset points", ha="center", va="bottom", fontsize=7.8,
                         color=INK2)
        ticks.append(y), labels.append("parts separately")
        y += 1
        ax2.barh(y, s_["joint"], height=0.7, color="#52514e", linewidth=0, zorder=2)
        ax2.annotate(f"{num(s_['joint'])} ({s_['pct']:+d}%)".replace("-", "−"), (s_["joint"], y), xytext=(5, 0),
                     textcoords="offset points", va="center", fontsize=8.5, color=INK)
        ticks.append(y), labels.append("all parts as one disruption")
        y += 1
        ax2.barh(y, s_["real"], height=0.7, color=col("none"), linewidth=0, zorder=2)
        ax2.annotate(num(s_["real"]), (s_["real"], y), xytext=(5, 0), textcoords="offset points", va="center",
                     fontsize=8.5, color=INK)
        ticks.append(y), labels.append(f"real sleep in {cname}")
        y += 1.4
    ax2.set_yticks(ticks, labels)
    for k, t in enumerate(ax2.get_yticklabels()):
        t.set_color(INK)
        if k in heads:
            t.set_fontweight("bold")
            t.set_color(INK2)
    ax2.tick_params(axis="y", length=0, labelsize=9.2)
    ax2.set_ylim(y - 0.9, -0.8)
    ax2.axvline(0, color=AXIS, linewidth=1)
    ax2.set_xlim(0, max([sum(A.get(p_, {}).get("p50", 0) for p_ in s_["parts"]) for s_ in synth.values()] + [1000])
                 * 1.15)
    ax2.set_xlabel("first-call penalty, ns (p50)")
    ax2.set_title("b) Synthetic C-states")
    legend(fig, [plt.Rectangle((0, 0), 1, 1, color=BAR, label="single disruption"),
                 plt.Rectangle((0, 0), 1, 1, color=CTRL, label="control"),
                 plt.Rectangle((0, 0), 1, 1, color=shades[1], label="parts of the synthetic state (from section A)"),
                 plt.Rectangle((0, 0), 1, 1, color="#52514e", label="synthetic, as one disruption"),
                 plt.Rectangle((0, 0), 1, 1, color=col("none"), label="real sleep, no warm-up")])
    return save(fig, name, a.out)


# ---------------------------------------------------------------- 20. tick: time since the last interrupt
def fig20(a):
    name = "fig20_tick_recency.png"
    sets = [(10, runs("nztB-10") + runs("nztT-10")), (100, runs("nztB-100") + runs("nztT-100"))]
    if not all(rr for _, rr in sets):
        return skip(name, "no runs *-nztB-10, *-nztT-10, *-nztB-100, *-nztT-100")
    edges = np.array([0, 5, 10, 20, 50, 100, 200, 300, 500, 700, 1000])
    mids = np.sqrt(np.maximum(edges[:-1], 2) * edges[1:])
    title = "The tick doubles the spin penalty, but only once ~100 µs have passed since it; with no interrupt in the pause, ~55 ns"
    sub = ("Intel i7-8700, bench, handler S, POLL policy, booted with nohz_full=2-5; runs with and without the tick "
           "are pooled (without the tick the core still got ~850 interrupts/s).\n"
           "x axis: time from the last interrupt in the loop (gap > 1 µs) to the event. Line: p50, "
           "band: p10–p90; horizontal line: trials with no interrupt at all during the pause.")
    fig, rect = frame(14, 5.9, title, sub, left=0.95, right=0.35, bottom=0.8)
    gs = fig.add_gridspec(1, 2, wspace=0.18, **rect)
    for i, (dt, rr) in enumerate(sets):
        ax = fig.add_subplot(gs[i])
        style(ax, grid="both")
        modes = ["spin", "spinshadow"] if any(r.has("spinshadow", dt) for r in rr) else ["spin"]
        for m in modes:
            ex = np.concatenate([r.excess(m, dt) for r in rr if r.has(m, dt)])
            gap = np.concatenate([r.cells[(m, dt)]["gap_us"] for r in rr if r.has(m, dt)])
            xs, p50, lo, hi = [], [], [], []
            for k in range(len(edges) - 1):
                v = ex[(gap >= edges[k]) & (gap < edges[k + 1])]
                if len(v) < 100:
                    continue
                xs.append(mids[k]), p50.append(q(v, .5)), lo.append(q(v, .1)), hi.append(q(v, .9))
            ax.fill_between(xs, lo, hi, color=col(m), alpha=.12, linewidth=0, zorder=1)
            ax.plot(xs, p50, color=col(m), marker=mk(m), markersize=7, markeredgecolor=SURF, zorder=3)
            clean = ex[gap < 0]
            if len(clean) >= 100:
                c50 = q(clean, .5)
                ax.axhline(c50, color=col(m), linestyle=":", linewidth=1.6, zorder=2)
                ax.annotate(f"no interrupts: {num(c50)} ns (n = {len(clean)})", (edges[1], c50),
                            xytext=(0, 5), textcoords="offset points", fontsize=8.5, color=INK2)
            if m == "spin":
                ax.annotate(f"{num(p50[-1])} ns", (xs[-1], p50[-1]), xytext=(8, 0), textcoords="offset points",
                            va="center", fontsize=9, color=INK)
        ax.set_xscale("log")
        ax.set_xticks([5, 10, 20, 50, 100, 200, 500, 1000], ["5", "10", "20", "50", "100", "200", "500", "1000"])
        ax.set_xlim(2, 1500)
        ax.set_ylim(0, 170 if dt == 10 else 190)
        ax.set_xlabel("from the last interrupt to the event, µs")
        if i == 0:
            ax.set_ylabel("first call − hot, ns")
        n = sum(len(r.ns("spin", dt)) for r in rr)
        ax.set_title(f"{'ab'[i]}) Δt = {dt} ms, {n} spin trials")
    legend(fig, [key("spin"), key("spinshadow")])
    return save(fig, name, a.out)


FIGS = [("1", fig01), ("2", fig02), ("3", fig03), ("4", fig04), ("5", fig05), ("6", fig06), ("7", fig07a),
        ("7", fig07b), ("8", fig08), ("9", fig09), ("10", fig10), ("11", fig11), ("12", fig12), ("13", fig13),
        ("14", fig14), ("15", fig15), ("16", fig16), ("17", fig17), ("18", fig18), ("19", fig19), ("20", fig20)]


def main():
    global ROOT
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated figure numbers, e.g. 1,5,7")
    ap.add_argument("--results", default=ROOT, help="directory of Intel runs (cold-path/results)")
    ap.add_argument("--amd", default=AMD, help="directory of AMD runs (z1-*, z2-*)")
    ap.add_argument("--online", default=ONLINE, help="export.csv of the online stand")
    ap.add_argument("--series", help="online-stand series for figures 8–10 (default: the most complete one)")
    ap.add_argument("--out", default=OUT, help="where to save the PNGs")
    a = ap.parse_args()
    ROOT = a.results
    setup_style()
    only = set(a.only.split(",")) if a.only else None
    for num_, fn in FIGS:
        if only and num_ not in only:
            continue
        print(f"{fn.__name__}:")
        fn(a)


if __name__ == "__main__":
    main()
