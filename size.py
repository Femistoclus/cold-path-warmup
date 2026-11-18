#!/usr/bin/env python3
"""Summary of the size.sh series: the handler-size axis.

Runs: results/<MMDD-HHMMSS>-sz*, per name the latest finished one (bench.log has the 100% progress line); missing
and unfinished runs are skipped, so the script also works while the series is still running.
Cell: median over runs (min–max across runs); times in µs; penalty = quantile(mode) − quantile(hot) of the same
run; event → end = late_us + first call; warm-up pass d = Σwarm_us / Σwakes, core share = d / T.

Usage: python3 size.py [results dir]   # markdown to stdout, compact CSV to <results dir>/size-summary.csv
"""
import csv
import glob
import math
import os
import re
import statistics
import sys

from analyze import DISCARD, load, mean, quantile

POINTS = ["cpptrader", "tx", "c32", "c128", "d64", "d64i", "big"]
LABEL = {"cpptrader": "S"}
RUNS = int(os.environ.get("RUNS", 3))  # process runs per cell in size.sh
CNT = {"b": ["cycles", "l1_miss", "l2_miss", "l3_miss", "ic_miss"],
       "c": ["code_l2_miss", "ic_stall", "baclears", "itlb_walk"]}
HANDLER = {  # what each point runs: CMakeLists.txt, src/handler/
    "S": "CppTrader, Add + Delete",
    "tx": "S + ~50-byte order, sendto",
    "c32": "S + 32 KB generated code",
    "c128": "S + 128 KB generated code",
    "c512": "S + 512 KB generated code",
    "d64": "synthetic: 1 MB table, 64 KB per event, dependent reads",
    "d64i": "synthetic: 1 MB table, 64 KB per event, independent reads",
    "d512": "synthetic: 16 MB table, 512 KB per event, dependent reads",
    "big": "S + 128 KB code + 64 KB model (dependent reads) + risk + sendto",
}

# bench.log lines, matched on language-neutral tokens (old logs are in Russian):
#   handle trace: 946 instructions, 79 code lines in 24 ranges: ...
#   prefetch: set "dc", passes 1; data lines 48 (known before the event 45), code lines 79
#   warming: shadow every 50 us, ...
#    100%  (401 s)
TRACE = re.compile(r"handle(?: trace)?: (\d+) \S+, (\d+) ")
DATA = re.compile(r"^[^;\n]*;\D*?(\d+) \(", re.M)
PERIOD = re.compile(r"\bshadow \S+ (\d+) ")
FINISHED = re.compile(r"^\s*100%\s+\(", re.M)

DIRS = {}       # run name → directory of the latest finished run
UNFINISHED = [] # no 100% line in bench.log: still running, not copied yet, or failed
CACHE = {}
BROKEN = []     # unreadable calls.csv
ROWS = []       # (point, series, mode, metric, value) → size-summary.csv


class Missing(Exception):
    """The run lacks the mode or the field."""


class Run:
    def __init__(self, path):
        self.first, _, self.extra = load(os.path.join(path, "calls.csv"))
        with open(os.path.join(path, "bench.log"), errors="replace") as f:
            self.log = f.read()

    def key(self, mode):
        keys = [k for k, v in self.first.items() if k[0] == mode and v]
        if not keys:
            raise Missing(mode)
        return max(keys, key=lambda k: k[1])

    def x(self, mode):
        """First-call times, µs."""
        return self.first[self.key(mode)]

    def field(self, mode, name):
        v = self.extra[self.key(mode)].get(name)
        if not v:
            raise Missing(name)
        return v

    def q(self, mode, p):
        return quantile(self.x(mode), p)

    def pen(self, mode, p):
        return self.q(mode, p) - self.q("hot", p)

    def e2e(self, mode):
        return [late + x for late, x in zip(self.field(mode, "late_us"), self.x(mode))]

    def d(self, mode):
        """One warm-up pass (prewake: all K passes), µs."""
        wakes = sum(self.field(mode, "wakes"))
        if not wakes:
            raise Missing("wakes")
        return sum(self.field(mode, "warm_us")) / wakes

    def period(self):
        m = PERIOD.search(self.log)
        if not m:
            raise Missing("period")
        return int(m.group(1))

    def cost(self, mode):
        """Warm-up share of the core, %."""
        return 100 * self.d(mode) / self.period()

    def removed(self, base, mode, p):
        """Share of the base penalty removed by mode, %; only when the base penalty is above 50 ns."""
        b = self.pen(base, p)
        if b <= 0.05:
            raise Missing("small base penalty")
        return 100 * (b - self.pen(mode, p)) / b


def scan(root):
    for path in sorted(glob.glob(os.path.join(root, "*-sz*"))):
        m = re.fullmatch(r"\d{4}-\d{6}-(sz\S+)", os.path.basename(path))
        log = os.path.join(path, "bench.log")
        if not m or not os.path.exists(os.path.join(path, "calls.csv")) or not os.path.exists(log):
            continue
        with open(log, errors="replace") as f:
            if FINISHED.search(f.read()):
                DIRS[m.group(1)] = path  # sorted by time: the latest wins
            else:
                UNFINISHED.append(m.group(1))


def run(name):
    if name not in DIRS:
        return None
    if name not in CACHE:
        try:
            CACHE[name] = Run(DIRS[name])
        except (ValueError, KeyError, csv.Error) as e:
            BROKEN.append(f"{name}: {e}")
            CACHE[name] = None
    return CACHE[name]


def runs(regex):
    return [r for r in (run(n) for n in sorted(DIRS) if re.fullmatch(regex, n)) if r]


def label(p):
    return LABEL.get(p, p)


def us(v):
    return f"{v:.3f}" if abs(v) < 1 else f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}"


def pct(v):
    return f"{v:.0f}"


def ns(v):
    return str(round(1000 * v))


def num(v):
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}" if abs(v) >= 1 else f"{v:.2f}"


def stat(rs, fn, key=None, fmt=us):
    """Table cell: median of fn over runs (min–max); key = (point, series, mode, metric) also goes to the CSV."""
    vals = []
    for r in rs:
        try:
            v = fn(r)
        except Missing:
            continue
        if v is not None and not math.isnan(v):
            vals.append(v)
    if not vals:
        return "—"
    med = statistics.median(vals)
    if key:
        point, series, mode, metric = key
        ROWS.append((label(point), series, mode, metric, med))
        if len(vals) > 1:
            ROWS.append((label(point), series, mode, metric + "_min", min(vals)))
            ROWS.append((label(point), series, mode, metric + "_max", max(vals)))
    text = fmt(med) if len(vals) == 1 else f"{fmt(med)} ({fmt(min(vals))}–{fmt(max(vals))})"
    return text if len(vals) == len(rs) else f"{text} [{len(vals)} of {len(rs)}]"  # the rest: no mode or base ≤ 50 ns


def table(head, rows):
    if not rows:
        print("_no data_\n")
        return
    print("| " + " | ".join(head) + " |")
    print("|" + "|".join(" --- " for _ in head) + "|")
    for row in rows:
        print("| " + " | ".join(row) + " |")
    print()


def pen_cells(rs, point, series, mode):
    """Penalty to hot, p50 and p99."""
    return [stat(rs, lambda r, q=q: r.pen(mode, q), (point, series, mode, f"pen_p{q * 100:g}_us")) for q in (.5, .99)]


def removed_cells(rs, point, series, mode, base):
    """Share of the base penalty removed by mode, p50 and p99, %."""
    return [stat(rs, lambda r, q=q: r.removed(base, mode, q), (point, series, mode, f"removed_p{q * 100:g}_pct"), pct)
            for q in (.5, .99)]


def e2e_cells(rs, point, series, mode, qs=(.5, .99)):
    return [stat(rs, lambda r, p=p: quantile(r.e2e(mode), p), (point, series, mode, f"e2e_p{p * 100:g}_us"))
            for p in qs]


# 0. What has arrived.

def readiness():
    expect = {"N": 2, "B": 6 * (RUNS + 2), "A": 6 * RUNS, "P": 6, "T": 8, "R": 6, "F": 8, "W": 12, "K": 12}
    found = {s: sum(1 for n in DIRS if n.startswith("sz" + s + "-")) for s in expect}
    print("Runs found / planned in size.sh: " + ", ".join(f"{s} {found[s]}/{n}" for s, n in expect.items()))
    late = [n for n in UNFINISHED if n not in DIRS]
    if late:
        print(f"No 100% line in bench.log (running, not copied yet, or failed; skipped): {', '.join(late)}")
    print(f"The first {DISCARD} trials of each run are dropped; a cell is the median over runs (min–max); "
          "p99 of one run with 1000 trials rests on ~10 points; [k of n]: the value exists in k runs of n "
          "(removed share only when the control penalty is above 50 ns).\n")


# 1. Passport.

def passport():
    print("## 1. Points\n")
    rows = []
    for p in POINTS:
        rs = runs(rf"szB-{p}-r\d+") + runs(rf"sz[A-Z]-{p}-.*")
        if not rs:
            continue
        log = rs[0].log
        trace = TRACE.search(log)
        data = DATA.search(log)
        text = HANDLER.get(label(p), "—")
        cells = [trace.group(1) if trace else "—", trace.group(2) if trace else "—", data.group(1) if data else "—"]
        for name, v in zip(("instructions", "code_lines", "data_lines"), cells):
            if v != "—":
                ROWS.append((label(p), "passport", "-", name, int(v)))
        b, a = runs(rf"szB-{p}-r\d+"), runs(rf"szA-{p}-r\d+")
        rows.append([label(p)] + cells + [text,
                     stat(b, lambda r: r.q("hot", .5), (p, "B", "hot", "first_p50_us")),
                     stat(a, lambda r: r.q("hot", .5), (p, "A", "hot", "first_p50_us")),
                     stat(b, lambda r: r.d("spinshadow"), (p, "B", "spinshadow", "d_us")),
                     aa_delta(p)])
    table(["Point", "instructions", "code lines", "data lines", "handler", "hot p50, us (spin)",
           "hot p50, us (sleep)", "d, us", "δ A/A, us"], rows)


def aa_delta(p):
    """Largest difference of first-call p50 between szB-<p>-r1 and its repeat szB-<p>-aa."""
    a, b = run(f"szB-{p}-r1"), run(f"szB-{p}-aa")
    if not (a and b):
        return "—"
    modes = {k[0] for k in a.first} & {k[0] for k in b.first}
    diffs = [v for v in (try_(lambda m=m: abs(a.q(m, .5) - b.q(m, .5))) for m in modes) if v is not None]
    if not diffs:
        return "—"
    delta = max(diffs)
    ROWS.append((label(p), "B", "-", "aa_delta_us", delta))
    return us(delta)


# 2. Series B: spin (POLL), T = 50 µs.

def series_b():
    print("## 2. Series B: spin (POLL), T = 50 us\n")
    rows, share, phase = [], [], []
    for p in POINTS:
        rs = runs(rf"szB-{p}-r\d+")
        if rs:
            for i, m in enumerate(("sys", "spin", "spinshadow", "spinpf")):
                rows.append([label(p) if i == 0 else "", str(len(rs)) if i == 0 else "", m]
                            + pen_cells(rs, p, "B", m)
                            + [stat(rs, lambda r, m=m: 100 * r.pen(m, .5) / r.q("hot", .5),
                                    (p, "B", m, "pen_p50_hot_pct"), pct)])
            share.append([label(p)]
                         + removed_cells(rs, p, "B", "spinshadow", "spin")
                         + removed_cells(rs, p, "B", "spinpf", "spin")
                         + [stat(rs, lambda r: r.cost("spinshadow"), (p, "B", "spinshadow", "cost_pct"), num),
                            stat(rs, lambda r: r.d("spinpf"), (p, "B", "spinpf", "d_us")),
                            stat(rs, lambda r: r.cost("spinpf"), (p, "B", "spinpf", "cost_pct"), num)])
        ph = run(f"szB-{p}-phase")
        if ph:
            phase.append([label(p)] + phase_cells(p, rs, ph))
    table(["Point", "runs", "mode", "penalty p50, us", "penalty p99, us", "penalty p50 / hot, %"], rows)
    print("Share of the spin penalty removed by warm-up: (spin − X) / (spin − hot), %; cost: core share d / T, %\n")
    table(["Point", "spinshadow p50", "spinshadow p99", "spinpf p50", "spinpf p99", "spinshadow cost, %",
           "spinpf pass, us", "spinpf cost, %"], share)
    print("Random event phase (H4): spinshadow on the grid (runs r*) against a random phase (one run, 2000 "
          "trials). A collision is lateness above the grid's p99 lateness; expected share ≈ d / T.\n")
    table(["Point", "1st call p50: grid", "random", "1st call p99: grid", "random",
           "event → end p50: grid", "random", "event → end p99: grid", "random",
           "lateness threshold, us", "late: grid, %", "random, %", "d / T, %"], phase)


def phase_cells(p, grid, ph):
    m = "spinshadow"
    late = [v for r in grid for v in (try_(lambda: r.field(m, "late_us")) or [])]
    thr = quantile(late, .99) if late else 0.5
    over = lambda r: 100 * sum(v > thr for v in r.field(m, "late_us")) / len(r.field(m, "late_us"))
    cells = []
    for f, name in ((lambda r, q: r.q(m, q), "first"), (lambda r, q: quantile(r.e2e(m), q), "e2e")):
        for q in (.5, .99):
            metric = f"{name}_p{q * 100:g}_us"
            cells += [stat(grid, lambda r: f(r, q), (p, "B", m, metric)),
                      stat([ph], lambda r: f(r, q), (p, "B-phase", m, metric))]
    return cells + [us(thr), stat(grid, over, (p, "B", m, "late_over_pct"), num),
                    stat([ph], over, (p, "B-phase", m, "late_over_pct"), num),
                    stat([ph], lambda r: r.cost(m), fmt=num)]


# 3. Series A: sleep (all states, policy 8).

def series_a():
    print("## 3. Series A: sleep (all states), shadow T = 1 ms\n")
    rows, extra = [], []
    for p in POINTS:
        rs = runs(rf"szA-{p}-r\d+")
        if not rs:
            continue
        for i, m in enumerate(("none", "shadow", "prewake", "pfwake")):
            rows.append([label(p) if i == 0 else "", str(len(rs)) if i == 0 else "", m]
                        + pen_cells(rs, p, "A", m) + e2e_cells(rs, p, "A", m)
                        + (removed_cells(rs, p, "A", m, "none") if m != "none" else ["", ""]))
        extra.append([label(p),
                      stat(rs, lambda r: r.q("hot", .5)),
                      stat(rs, lambda r: quantile(r.field("none", "late_us"), .5), (p, "A", "none", "late_p50_us")),
                      stat(rs, lambda r: 100 * r.pen("none", .5) / quantile(r.field("none", "late_us"), .5),
                           (p, "A", "none", "pen_late_pct"), num),
                      stat(rs, lambda r: r.d("prewake"), (p, "A", "prewake", "d_us")),
                      stat(rs, lambda r: r.d("pfwake"), (p, "A", "pfwake", "d_us")),
                      stat(rs, lambda r: r.cost("shadow"), (p, "A", "shadow", "cost_pct"), num)])
    table(["Point", "runs", "mode", "penalty p50, us", "penalty p99, us", "event → end p50, us",
           "event → end p99, us", "removed of none p50, %", "removed of none p99, %"], rows)
    print("H2: none penalty against wake-up lateness; warm-up cost.\n")
    table(["Point", "hot p50, us", "none lateness p50, us", "none penalty / lateness, %",
           "prewake pass, us", "pfwake pass, us", "shadow cost, % of core"], extra)


# 4. Series T: shadow period in the spin loop.

def series_t():
    print("## 4. Series T: remaining spinshadow penalty by period (POLL)\n")
    rows, verdict = [], []
    for p in POINTS:
        cells = [(50, runs(rf"szB-{p}-r\d+"))]
        ts = sorted({int(n.rsplit("-T", 1)[1]) for n in DIRS if re.fullmatch(rf"szT-{p}-T\d+", n)})
        cells += [(t, runs(rf"szT-{p}-T{t}")) for t in ts]
        cells = [(t, rs) for t, rs in cells if rs]
        if not cells or not (ts or p == "cpptrader"):  # S: only T = 50 here, its T sweep is f0-ss*
            continue
        good = []
        for t, rs in cells:
            m = f"spinshadow@{t}"
            remain = lambda r, q: 100 - r.removed("spin", "spinshadow", q)
            r50 = [v for v in (try_(lambda: remain(r, .5)) for r in rs) if v is not None]
            if r50 and statistics.median(r50) <= 20:
                good.append(t)
            rows.append([label(p), str(t), str(len(rs)),
                         stat(rs, lambda r: r.pen("spin", .5), (p, "T", f"spin@{t}", "pen_p50_us")),
                         stat(rs, lambda r: r.pen("spinshadow", .5), (p, "T", m, "pen_p50_us")),
                         stat(rs, lambda r: r.pen("spinshadow", .99), (p, "T", m, "pen_p99_us")),
                         stat(rs, lambda r: remain(r, .5), (p, "T", m, "remain_p50_pct"), pct),
                         stat(rs, lambda r: remain(r, .99), (p, "T", m, "remain_p99_pct"), pct),
                         stat(rs, lambda r: r.d("spinshadow"), (p, "T", m, "d_us")),
                         stat(rs, lambda r: r.cost("spinshadow"), (p, "T", m, "cost_pct"), num)])
        ds = [v for t, rs in cells for v in (try_(lambda r=r: r.d("spinshadow")) for r in rs) if v is not None]
        d = statistics.median(ds) if ds else float("nan")
        t_rest = max(good) if good else None
        ok = [t for t in good if t >= 100 * d]
        verdict.append([label(p), us(d) if ds else "—", f"{100 * d:.0f}" if ds else "—",
                        str(t_rest) if t_rest else "none (remainder > 20% at every T)",
                        f"yes, T = {', '.join(map(str, ok))}" if ok else "no"])
    table(["Point", "T, us", "runs", "spin penalty p50, us", "spinshadow penalty p50, us",
           "spinshadow penalty p99, us", "remainder p50, % of spin", "remainder p99, % of spin", "d, us",
           "cost d / T, %"], rows)
    print("H4: T_rem is the largest measured T with a p50 remainder ≤ 20% of the spin penalty; at T ≥ 100·d "
          "collisions (share ≈ d / T ≤ 1%) stay out of p99.\n")
    table(["Point", "d, us", "100·d, us", "T_rem, us", "T ≥ 100·d with remainder ≤ 20%"], verdict)


def try_(fn):
    try:
        return fn()
    except Missing:
        return None


# 5. Series R: several passes in prewake.

def series_r():
    print("## 5. Series R: several passes in prewake (policy 8, δ = 400 us), H8\n")
    rows = []
    for p in POINTS:
        ks = sorted({int(n.rsplit("-K", 1)[1]) for n in DIRS if re.fullmatch(rf"szR-{p}-K\d+", n)})
        for k in ks:
            rs, m = runs(rf"szR-{p}-K{k}"), f"prewake@K{k}"
            rows.append([label(p), str(k),
                         stat(rs, lambda r: r.pen("none", .5), (p, "R", f"none@K{k}", "pen_p50_us")),
                         stat(rs, lambda r: r.pen("prewake", .5), (p, "R", m, "pen_p50_us")),
                         stat(rs, lambda r: r.pen("prewake", .99), (p, "R", m, "pen_p99_us")),
                         stat(rs, lambda r: 100 - r.removed("none", "prewake", .5), (p, "R", m, "remain_p50_pct"), num),
                         stat(rs, lambda r: 100 * r.pen("prewake", .5) / r.q("hot", .5),
                              (p, "R", m, "remain_hot_pct"), num),
                         stat(rs, lambda r: r.d("prewake"), (p, "R", m, "d_us"))])
    table(["Point", "K", "none penalty p50, us", "prewake penalty p50, us", "prewake penalty p99, us",
           "remainder, % of none", "remainder, % of hot", "K passes, us"], rows)


# 6. Series F: prefetch after C3.

def series_f():
    print("## 6. Series F: prefetch after C3 (policy 3), H6, H7\n")
    rows = []
    for p in POINTS:
        sets = sorted({n.split("-")[2] for n in DIRS if re.fullmatch(rf"szF-{p}-\w+-r\d+", n)})
        for s in sets:
            rs, series = runs(rf"szF-{p}-{s}-r\d+"), f"F-{s}"
            for i, m in enumerate(("none", "prewake", "pfwake", "pfevent")):
                rows.append([label(p) if i == 0 else "", s if i == 0 else "", str(len(rs)) if i == 0 else "", m]
                            + pen_cells(rs, p, series, m)
                            + [removed_cells(rs, p, series, m, "none")[0] if m != "none" else "",
                                           stat(rs, lambda r: 100 * r.q(m, .5) / r.q("none", .5),
                                                (p, series, m, "first_none_pct"), num)]
                            + e2e_cells(rs, p, series, m)
                            + [stat(rs, lambda r: r.d(m), (p, series, m, "d_us")) if m in ("prewake", "pfwake") else ""])
    table(["Point", "set", "runs", "mode", "penalty p50, us", "penalty p99, us", "removed of none p50, %",
           "1st call p50, % of none", "event → end p50, us", "event → end p99, us", "warm-up, us"], rows)


# 7. Series W: warming the send path.

def series_w():
    print("## 7. Series W: send-path warm-up W0 / W1 / W2, H11\n")
    loop, sleep = [], []
    for p in POINTS:
        for w in (0, 1, 2):
            series = f"W{w}"
            rs = runs(rf"szW-{p}-w{w}-spin")
            if rs:
                loop.append([label(p), series]
                            + pen_cells(rs, p, series, "spin") + pen_cells(rs, p, series, "spinshadow")
                            + [stat(rs, lambda r: 100 - r.removed("spin", "spinshadow", .5),
                                    (p, series, "spinshadow", "remain_p50_pct"), num),
                               stat(rs, lambda r: r.d("spinshadow"), (p, series, "spinshadow", "d_us"))])
            rs = runs(rf"szW-{p}-w{w}-sleep")
            if rs:
                sleep.append([label(p), series]
                             + pen_cells(rs, p, series, "none") + pen_cells(rs, p, series, "prewake")
                             + [stat(rs, lambda r: 100 - r.removed("none", "prewake", .5),
                                     (p, series, "prewake", "remain_p50_pct"), num),
                                stat(rs, lambda r: r.d("prewake"), (p, series, "prewake", "d_us"))])
    print("Spin (POLL), spinshadow T = 1 ms:\n")
    table(["Point", "W", "spin p50, us", "spin p99, us", "spinshadow p50, us", "spinshadow p99, us",
           "remainder p50, % of spin", "d, us"], loop)
    print("Sleep (policy 8), prewake 400 us ahead:\n")
    table(["Point", "W", "none p50, us", "none p99, us", "prewake p50, us", "prewake p99, us",
           "remainder p50, % of none", "pass, us"], sleep)


# 8. Series P: tails on 20 thousand trials.

def series_p():
    print("## 8. Series P: tails, 20 thousand trials per cell (spin Δt = 1 ms, sleep Δt = 10 ms)\n")
    rows = []
    qs = (.5, .99, .999)
    for p in POINTS:
        cells = [("hot (spin)", "spin", "hot"), ("spin", "spin", "spin"), ("spinshadow, grid", "spin", "spinshadow"),
                 ("spinshadow, random phase", "phase", "spinshadow"), ("hot (sleep)", "sleep", "hot"),
                 ("none", "sleep", "none"), ("prewake", "sleep", "prewake")]
        for name, part, m in cells:
            r = run(f"szP-{p}-{part}")
            if not r or try_(lambda: r.x(m)) is None:
                continue
            mode = f"{m}-random" if part == "phase" else f"{m}-{part}" if m == "hot" else m
            e2e = r.e2e(m) if m != "hot" else r.x(m)
            rows.append([label(p), name, str(len(r.x(m)))]
                        + [stat([r], lambda r, q=q: r.q(m, q), (p, "P", mode, f"first_p{q * 100:g}_us")) for q in qs]
                        + [stat([r], lambda r, q=q: quantile(e2e, q), (p, "P", mode, f"e2e_p{q * 100:g}_us"))
                           for q in qs])
    table(["Point", "cell", "trials", "1st call p50, us", "p99", "p999", "event → end p50, us", "p99",
           "p999"], rows)


# 9. Series K: counters.

def series_k():
    print("## 9. Series K: 1st-call counters, mean (group b: data and L1i, group c: front end)\n")
    cols = CNT["b"] + CNT["c"]
    rows = []
    for p in POINTS:
        for state, modes in (("B", ("hot", "spin", "spinshadow", "spinpf")), ("A", ("hot", "none", "prewake", "pfwake"))):
            groups = {g: run(f"szK-{p}-{state}-{g}") for g in CNT}
            if not any(groups.values()):
                continue
            for m in modes:
                vals = {}
                for g, r in groups.items():
                    for c in CNT[g]:
                        v = try_(lambda: mean(r.field(m, c))) if r else None
                        if v is not None and c not in vals:
                            vals[c] = v
                            ROWS.append((label(p), f"K-{state}", m, c, v))
                if vals:
                    rows.append([label(p), "spin" if state == "B" else "sleep", m]
                                + [f"{vals[c]:.0f}" if c in vals else "—" for c in cols])
    table(["Point", "state", "mode"] + cols, rows)


# 10. Null control.

def series_n():
    print("## 10. Control: empty handler (cost of the measurement window), ns\n")
    rows = []
    for name, modes in (("szN-B", ("hot", "sys", "spin", "spinshadow", "spinpf")),
                        ("szN-A", ("hot", "none", "shadow", "prewake", "pfwake"))):
        r = run(name)
        for m in modes:
            if not r or try_(lambda: r.x(m)) is None:
                continue
            key = (lambda metric: ("null", name[2:], m, metric))
            rows.append([name, m, stat([r], lambda r: r.q(m, .5), key("first_p50_us"), ns),
                         stat([r], lambda r: r.q(m, .99), key("first_p99_us"), ns),
                         "" if m == "hot" else stat([r], lambda r: r.pen(m, .5), key("pen_p50_us"), ns),
                         "" if m == "hot" else stat([r], lambda r: r.pen(m, .99), key("pen_p99_us"), ns)])
    table(["Run", "mode", "1st call p50, ns", "1st call p99, ns", "penalty p50, ns", "penalty p99, ns"], rows)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "results"
    scan(root)
    print(f"# Handler size axis: size.sh summary ({root})\n")
    readiness()
    for part in (passport, series_b, series_a, series_t, series_r, series_f, series_w, series_p, series_k, series_n):
        part()
    if BROKEN:
        print("Unreadable: " + "; ".join(BROKEN))
    out = os.path.join(root, "size-summary.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["point", "series", "mode", "metric", "value"])
        w.writerows((p, s, m, k, f"{v:.6g}") for p, s, m, k, v in ROWS)
    print(f"\nCSV for figures: {out} ({len(ROWS)} rows)")


if __name__ == "__main__":
    main()
