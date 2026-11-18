#!/usr/bin/env python3
"""Summary of the iso-night.sh series (stage 1, no CAT): isolation level × neighbour × warm-up.

Runs results/<time>-i<series>-<level>-<load>-p<idle policy>-<kind>-r<N>/; a cell is (series, level, load, policy,
kind), its runs (r1, r2, …) are pooled. Per mode, first call after the pause:
  - penalty = quantile of (window − p50 hot of the same run), p50 / p99 / p999, ns;
  - event → end of first call = late_us + window, µs; lateness (late_us) alone with its maximum;
  - neighbour loss = 1 − work during the trial / (solo speed × (Δt + lateness)); the solo speed comes from the
    i<series>-solo-<load>-c<core> runs (core CPU for shared, CPU−1 for l3); "to none": against none of the same cell;
  - core share taken by warm-up: warm_us / Δt (prewake, pfwake: δ / Δt);
  - δ — spread of the p50 penalty between the runs of a cell.
A quantile is printed only with ≥ 10 points beyond it, otherwise "—" (p99 needs 1000 trials, p999 — 10 thousand).
Controls per run: neighbour speed CV over 100 ms intervals, its involuntary switches, C1E+ entries and
non-timer interrupts on the measurement core.

Usage: python3 iso.py [results dir]
"""
import csv
import glob
import os
import re
import statistics
import sys
from collections import defaultdict

from analyze import DISCARD, quantile

RUN = re.compile(r"^\d{4}-\d{6}-i(?P<series>\d+)-(?P<level>shared|ded|os|osirq|l3)-(?P<load>[a-z0-9]+)"
                 r"-p(?P<pol>\d+)-(?P<kind>[A-Za-z0-9]+)-r(?P<rep>\d+)$")
SOLO = re.compile(r"^\d{4}-\d{6}-i(?P<series>\d+)-solo-(?P<load>[a-z0-9]+)-c(?P<core>\d+)$")
MODES = ["hot", "none", "empty", "shadow", "prewake", "pfwake", "pfevent", "spin", "spinshadow", "spinpf"]
HOLD = ("prewake", "pfwake")  # hold the core from wake-up (δ before the event) to the event


def q(xs, p):
    """Quantile if at least 10 points lie beyond it, else None."""
    return quantile(xs, p) if xs and len(xs) * (1 - p) >= 10 else None


def fmt(v, digits=0):
    return "—" if v is None else f"{v:.{digits}f}"


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def env_info(d):
    """isolation line (level, cpu, nb_cpu) and δ (--prewake-us) from env.txt."""
    env = read(os.path.join(d, "env.txt"))
    iso = re.search(r"^isolation: .*$", env, re.M)
    cpu = re.search(r"\bcpu=(\d+)", iso.group(0)) if iso else None
    nb_cpu = re.search(r"\bnb_cpu=(\d+)", iso.group(0)) if iso else None
    prewake = re.findall(r"--prewake-us (\d+)", env)
    return {"cpu": int(cpu.group(1)) if cpu else 5, "nb_cpu": int(nb_cpu.group(1)) if nb_cpu else 4,
            "prewake_us": int(prewake[-1]) if prewake else 200}


def load_run(d):
    """(mode, Δt) → {ex: window − hot p50 (ns), e2e (µs), late (µs), nb, warm, wakes}."""
    rows = defaultdict(lambda: defaultdict(list))
    with open(os.path.join(d, "calls.csv")) as f:
        for r in csv.DictReader(f):
            if r["call"] != "1" or int(r["trial"]) < DISCARD or r.get("dis", "-") != "-":
                continue
            c = rows[(r["cond"], int(r["dt_ms"]))]
            c["ns"].append(float(r["ns"]))
            c["late"].append(float(r["late_us"]))
            c["warm"].append(float(r["warm_us"]))
            c["wakes"].append(float(r["wakes"]))
            if "nb_work" in r:
                c["nb"].append(float(r["nb_work"]))
    hot = rows.get(("hot", 0))
    if not hot:
        return {}
    h50 = quantile(hot["ns"], .5)
    for c in rows.values():
        c["ex"] = [x - h50 for x in c["ns"]]
        c["e2e"] = [l + x / 1000 for l, x in zip(c["late"], c["ns"])]
    return rows


def solo_speeds(root):
    """(load, core) → [units/s] in run order."""
    out = defaultdict(list)
    for d in sorted(glob.glob(os.path.join(root, "*-i*-solo-*"))):
        m = SOLO.match(os.path.basename(d))
        rate = re.search(r"rate_per_s=([\d.]+)", read(os.path.join(d, "nb.out")))
        if m and rate:
            out[(m["load"], int(m["core"]))].append(float(rate.group(1)))
    return out


def controls(d, cpu):
    """CV of the neighbour log, involuntary switches, C1E+ entries and non-timer IRQs on core cpu."""
    res = {}
    log = [float(line.split()[1]) for line in read(os.path.join(d, "nb.log")).splitlines() if len(line.split()) == 2]
    log = log[2:-1]
    if len(log) > 3 and statistics.mean(log) > 0:
        res["cv"] = 100 * statistics.pstdev(log) / statistics.mean(log)
    sw = re.search(r"nonvoluntary_ctxt_switches:\s+(\d+)", read(os.path.join(d, "nb-status.txt")))
    if sw:
        res["nvcsw"] = int(sw.group(1))

    def idle(name):
        out = {}
        for line in read(os.path.join(d, name)).splitlines():
            m = re.match(r"(\S+) disable=\d+ usage=(\d+)", line)
            if m:
                out[m.group(1)] = int(m.group(2))
        return out

    a, b = idle("idle-before.txt"), idle("idle-after.txt")
    if a and b:
        res["deep"] = sum(b[k] - a.get(k, 0) for k in b if k not in ("POLL", "C1", "C1_ACPI"))

    def irqs(name):
        out = {}
        for line in read(os.path.join(d, name)).splitlines()[1:]:
            f = line.split()
            if len(f) > cpu + 1 and f[0].endswith(":") and f[0] != "LOC:" and f[cpu + 1].isdigit():
                out[f[0]] = int(f[cpu + 1])
        return out

    a, b = irqs("irq-before.txt"), irqs("irq-after.txt")
    if a and b:
        res["irq"] = sum(v - a.get(k, 0) for k, v in b.items())
    return res


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "results"
    solo = solo_speeds(root)
    cells = defaultdict(list)  # (series, level, load, pol, kind) → [(rep, dir, data, info)]
    for d in sorted(glob.glob(os.path.join(root, "*-i*-*-p*-r*"))):
        m = RUN.match(os.path.basename(d))
        if not m or not os.path.exists(os.path.join(d, "calls.csv")):
            continue
        data = load_run(d)
        if data:
            key = (m["series"], m["level"], m["load"], m["pol"], m["kind"])
            cells[key].append((m["rep"], d, data, env_info(d)))

    print("Neighbour alone, work units per second (runs in time order):")
    for (load, core), v in sorted(solo.items()):
        drift = 100 * (v[-1] - v[0]) / v[0] if len(v) > 1 and v[0] else 0.0
        print(f"  {load:<9} core {core}: " + ", ".join(f"{x:.0f}" for x in v) + f"; drift {drift:+.1f}%")
    print()

    best = {}
    pen50 = defaultdict(dict)  # (level, load, pol, kind, mode, Δt) → {series: p50 penalty}
    has_main = any(k[0] != "0" for k in cells)
    for key in sorted(cells, key=lambda k: (int(k[0]), k[1], k[2], int(k[3]), k[4])):
        series, level, load, pol, kind = key
        runs = cells[key]
        info = runs[0][3]
        core = info["nb_cpu"] if level == "l3" else info["cpu"]
        speed = statistics.mean(solo[(load, core)]) if solo.get((load, core)) else None
        print(f"[i{series}] {level} · neighbour {load} · policy {pol} · {kind} · runs {len(runs)}")
        print(f"  {'mode':<11}{'Δt':>4}{'n':>7} | {'penalty p50/p99/p999, ns':>24} |"
              f" {'event→end p50/p99/p999, us':>32} |"
              f" {'lateness p50/p99/p999/max, us':>32} | {'loss to solo/to none, %':>23} | {'core, %':>7} | δ p50, ns")
        modes = sorted({mk for _, _, data, _ in runs for mk in data},
                       key=lambda mk: (mk[1], MODES.index(mk[0]) if mk[0] in MODES else 99))
        for mode, dt in modes:
            pooled = defaultdict(list)
            for _, _, data, _ in runs:
                for k, v in data.get((mode, dt), {}).items():
                    pooled[k].extend(v)
            ex, e2e, late, nb = pooled["ex"], pooled["e2e"], pooled["late"], pooled["nb"]
            pen = "/".join(fmt(q(ex, p)) for p in (.5, .99, .999))
            lat = "/".join(fmt(q(e2e, p), 1) for p in (.5, .99, .999))
            lt = "/".join(fmt(q(late, p), 1) for p in (.5, .99, .999)) + f"/{max(late):.0f}"
            loss_solo = loss_none = None
            if nb and dt > 0:
                if speed:
                    loss_solo = 100 * (1 - statistics.mean(nb) / (speed * (dt / 1e3 + statistics.mean(late) / 1e6)))
                ref = []
                for _, _, data, _ in runs:
                    ref.extend(data.get(("none", dt), {}).get("nb", []))
                if ref and statistics.mean(ref) > 0:
                    loss_none = 100 * (1 - statistics.mean(nb) / statistics.mean(ref))
            share = None
            if dt > 0:
                share = (100 * info["prewake_us"] / (dt * 1e3) if mode in HOLD
                         else 100 * statistics.mean(pooled["warm"]) / (dt * 1e3))
            halves = [quantile(data[(mode, dt)]["ex"], .5) for _, _, data, _ in runs if (mode, dt) in data]
            spread = max(halves) - min(halves) if len(halves) > 1 else None
            loss = f"{fmt(loss_solo, 1)}/{fmt(loss_none, 1)}"
            pen50[(level, load, pol, kind, mode, dt)][series] = quantile(ex, .5)
            print(f"  {mode:<11}{dt:>4}{len(ex):>7} | {pen:>24} | {lat:>32} | {lt:>32} | {loss:>23} | {fmt(share, 2):>7} |"
                  f" {fmt(spread)}")
            if dt == 10 and mode != "hot" and not (has_main and series == "0"):
                cand = (quantile(e2e, .5), mode, pol, q(e2e, .99), q(e2e, .999), quantile(ex, .5), loss_solo, share)
                slot = (level, load)
                if slot not in best or cand[0] < best[slot][0]:
                    best[slot] = cand
        print("  controls: " + "; ".join(
            f"r{rep}: " + ", ".join(f"{k}={v:.1f}" if isinstance(v, float) else f"{k}={v}"
                                    for k, v in controls(d, inf["cpu"]).items())
            for rep, d, _, inf in runs))
        print()

    print('Summary at Δt = 10 ms: best mode by "event → end" p50 latency per level and neighbour')
    print(f"  {'level':<8}{'neighbour':<10}{'mode':<12}{'pol.':>4} | {'latency p50/p99/p999, us':>28} |"
          f" {'pen. p50, ns':>13} | {'loss, %':>9} | {'core, %':>7}")
    for (level, load), (e50, mode, pol, e99, e999, pen, loss, share) in sorted(best.items()):
        lat = f"{e50:.2f}/{fmt(e99, 2)}/{fmt(e999, 2)}"
        print(f"  {level:<8}{load:<10}{mode:<12}{pol:>4} | {lat:>28} | {pen:13.0f} | {fmt(loss, 1):>9} | {fmt(share, 2):>7}")
    ded = best.get(("ded", "no"))
    shared = {load: v for (lv, load), v in best.items() if lv == "shared"}
    if ded and shared:
        worst = max(shared, key=lambda k: shared[k][0])
        print(f"\nQuestion 1: best on ded: {ded[1]}, {ded[0]:.2f} us; best on shared: "
              f"{min(v[0] for v in shared.values()):.2f}–{shared[worst][0]:.2f} us (slowest with neighbour {worst}).")
    for (level, load, pol, kind, mode, dt), by in sorted(pen50.items()):
        if level == "ded" and mode == "spin" and (level, load, pol, kind, "spinshadow", dt) in pen50:
            other = pen50[(level, load, pol, kind, "spinshadow", dt)]
            for series in sorted(set(by) & set(other)):
                d = by[series] - other[series]
                print(f"Question 2 [i{series}, policy {pol}, Δt {dt}]: spin − spinshadow = {d:.0f} ns "
                      f"({'warm-up on top of isolation is needed' if d >= 80 else 'under 80 ns'})")
    aa = [(k, by) for k, by in sorted(pen50.items()) if "8" in by and len(by) > 1 and k[4] != "hot"]
    if aa:
        print("\nA/A and drift: p50 penalty of series 8 against the same cell in other series, ns")
        for (level, load, pol, kind, mode, dt), by in aa:
            rest = ", ".join(f"i{s_}: {v:.0f}" for s_, v in sorted(by.items()) if s_ != "8")
            print(f"  {level} {load} p{pol} {kind} {mode} Δt {dt}: i8 {by['8']:.0f}; {rest}")


if __name__ == "__main__":
    main()
