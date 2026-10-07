"""Test the book rules (scripts/research/book_families.py) with the same honest
protocol as discover.py: choose on the first 24 months, check the neighbours,
then look once at the 12 hidden months.

Also reports each rule "as written" (the book's own stop/target, no tuning),
pooled over all markets, so a rule cannot hide behind one lucky market.

Usage: python scripts/research/book_discover.py [MARKET ...]
Writes data/research/book_discovery.json (git-ignored).
"""

from __future__ import annotations

import json
import statistics
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research.book_families import LEVEL_FAMILIES, MARKET_FAMILIES  # noqa: E402
from scripts.research.discover import HOLD, PLANS, discovery_ok, holdout_ok  # noqa: E402
from scripts.research.engine import (  # noqa: E402
    BAR_SECONDS, YEAR2, bars, market, periods, simulate, simulate_levels, stats)
from scripts.research.families import Ind  # noqa: E402

MARKETS = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
           "EURJPY", "AUDJPY", "US100", "US500"]
OUT = ROOT / "data" / "research" / "book_discovery.json"
FADES = {"book_giant_bar_fade", "book_range_stoch"}
# The books' own plans: (target, valid bars, hold bars or None for the chart default).
AS_WRITTEN = {
    "book_outside_bar": (1.5, 0, None), "book_day_of_strength": (2.0, 0, 20), "book_band_pierce": (2.0, 0, None),
    "book_monday_panic": ("struct", 0, 2), "book_supply_demand": (3.0, 30, None),
    "book_supply_demand_trend": (3.0, 30, None), "book_ote": ("struct", 20, None), "book_symmetry": ("struct", 20, None),
}
LEVEL_TARGETS = [1.0, 1.5, 2.0, 3.0, "struct"]
VALIDS = [10, 30, 60]


def targets(f, sig: np.ndarray, entry: np.ndarray, stop: np.ndarray, struct: np.ndarray, mode, scale: float) -> np.ndarray:
    price = np.where(np.isfinite(entry), entry, f["c"].to_numpy())
    if mode == "struct":
        return price + (struct - price) * scale
    return price + sig * float(mode) * scale * np.abs(price - stop)


def market_family(data, f, x, a, label, make, tf, tested, candidates, written):
    signal = make(x)
    if (signal != 0).sum() < 20:
        return tested
    plan = (1.5, 1.0) if label in FADES else (1.5, 2.0)
    written.append((label, tf, simulate(data, f, signal, plan[0] * a, plan[1], HOLD[tf])))
    if (signal != 0).sum() < 40:
        return tested
    best = None
    for stop_atr, target_r in PLANS:
        tested += 1
        p = periods(simulate(data, f, signal, stop_atr * a, target_r, HOLD[tf]))
        if discovery_ok(p) and (best is None or p["disc"]["t"] > best[1]["disc"]["t"]):
            best = ((stop_atr, target_r, HOLD[tf]), p)
    if best is None:
        return tested
    (stop_atr, target_r, hold), p = best
    neighbours = []
    for s in (stop_atr * 0.75, stop_atr, stop_atr * 1.25):
        for g in (target_r * 0.75, target_r, target_r * 1.33):
            for h in (max(2, round(hold * 0.6)), hold, round(hold * 1.6)):
                neighbours.append(periods(simulate(data, f, signal, s * a, g, h))["disc"])
    candidates.append(summary(data.name, tf, label, f"stop {stop_atr:.1f}ATR target {target_r:.2f}R", hold, p, neighbours,
                              {"stop_atr": stop_atr, "target_r": target_r}))
    return tested


def level_family(data, f, x, label, make, tf, tested, candidates, written):
    sig, entry, stop, struct = make(x)
    if (sig != 0).sum() < 20:
        return tested
    limit = bool(np.isfinite(entry[sig != 0]).any())
    a = x.atr(14).to_numpy()
    seconds = BAR_SECONDS[tf]

    def run(mode, scale, valid, hold, pad=0.0):
        s = stop - sig * pad * a
        return simulate_levels(data, f, sig, entry, s, targets(f, sig, entry, s, struct, mode, scale), valid, hold, seconds)

    book_target, book_valid, book_hold = AS_WRITTEN[label]
    written.append((label, tf, run(book_target, 1.0, book_valid, book_hold or HOLD[tf])))
    if (sig != 0).sum() < 40:
        return tested
    has_struct = bool(np.isfinite(struct[sig != 0]).any())
    holds = [book_hold] if label == "book_monday_panic" else [HOLD[tf]]
    best = None
    for mode in LEVEL_TARGETS:
        if mode == "struct" and not has_struct:
            continue
        for valid in (VALIDS if limit else [0]):
            for hold in holds:
                tested += 1
                p = periods(run(mode, 1.0, valid, hold))
                if discovery_ok(p) and (best is None or p["disc"]["t"] > best[1]["disc"]["t"]):
                    best = ((mode, valid, hold), p)
    if best is None:
        return tested
    (mode, valid, hold), p = best
    neighbours = []
    for scale in (0.75, 1.0, 1.33):
        for h in (max(2, round(hold * 0.6)), hold, round(hold * 1.6)):
            for third in ((max(3, valid // 2), valid, valid * 2) if limit else (0.0, 0.25, 0.5)):
                if limit:
                    q = run(mode, scale, third, h)
                else:
                    q = run(mode, scale, 0, h, pad=third)
                neighbours.append(periods(q)["disc"])
    target_text = "structure" if mode == "struct" else f"{mode:.2f}R"
    candidates.append(summary(data.name, tf, label, f"target {target_text} limit {valid} bars", hold, p, neighbours,
                              {"target": mode, "valid": valid}))
    return tested


def summary(name, tf, label, plan, hold, p, neighbours, extra) -> dict:
    share = sum(1 for q in neighbours if q["avg"] > 0) / len(neighbours)
    median_t = statistics.median(q["t"] for q in neighbours)
    return {"market": name, "tf": tf, "family": label, "plan": plan, "hold_bars": hold,
            "robust": share >= 0.6 and median_t >= 1.0, "share": round(share, 2), "median_t": round(median_t, 2),
            **p, "holdout_ok": holdout_ok(p), **extra}


def line(c: dict) -> str:
    d, h, a = c["disc"], c["hold"], c["all"]
    return (f"{c['market']:7s} {c['tf']} {c['family']:26s} {c['plan']:34s} hold {c['hold_bars']:2d} | "
            f"24m: {d['n']:3d} tr {d['win']:.0%} {d['avg']:+.3f}R t={d['t']:.1f} | "
            f"hidden 12m: {h['n']:3d} tr {h['win']:.0%} {h['avg']:+.3f}R t={h['t']:.1f} pf={h['pf']:.2f} | "
            f"36m {a['total']:+.1f}R (neighbours {c['share']:.0%} ok)")


def run_market(name: str) -> dict:
    try:
        data = market(name)
    except Exception as exc:  # missing history
        return {"market": name, "error": str(exc), "tested": 0, "candidates": [], "written": []}
    tested, candidates, written = 0, [], []
    for tf in ("H1", "H4", "D1"):
        f = bars(data, tf)
        x = Ind(f)
        a = x.atr(14).to_numpy()
        for label, make, timeframes in MARKET_FAMILIES:
            if tf in timeframes:
                tested = market_family(data, f, x, a, label, make, tf, tested, candidates, written)
        for label, make, timeframes in LEVEL_FAMILIES:
            if tf in timeframes:
                tested = level_family(data, f, x, label, make, tf, tested, candidates, written)
    return {"market": name, "tested": tested, "candidates": candidates, "written": written}


def as_written(results: list[dict]) -> None:
    pooled: dict[tuple[str, str], list] = {}
    for r in results:
        for label, tf, trades in r.get("written", []):
            pooled.setdefault((label, tf), []).append(trades)
    print("\nEach rule exactly as the book wrote it, all markets together:")
    print(f"{'rule':28s} chart | first 24 months             | hidden 12 months")
    for (label, tf), parts in sorted(pooled.items()):
        arr = np.concatenate(parts)
        disc, hold = stats(arr[arr[:, 0] < YEAR2, 3]), stats(arr[arr[:, 0] >= YEAR2, 3])
        print(f"{label:28s} {tf}    | {disc['n']:5d} tr {disc['avg']:+.3f}R t={disc['t']:+5.1f} | "
              f"{hold['n']:5d} tr {hold['avg']:+.3f}R t={hold['t']:+5.1f}")


def main(names: list[str]) -> None:
    with Pool(3) as pool:
        results = pool.map(run_market, names)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps([{k: v for k, v in r.items() if k != "written"} for r in results], indent=1),
                   encoding="utf-8")
    tested = sum(r["tested"] for r in results)
    candidates = [c for r in results for c in r["candidates"]]
    robust = [c for c in candidates if c["robust"]]
    survivors = [c for c in robust if c["holdout_ok"]]
    for r in results:
        if r.get("error"):
            print(f"{r['market']}: no history ({r['error']})")
    print(f"\nTested {tested} book rule x plan versions on {len(results)} markets.")
    print(f"A. passed the 24-month discovery test: {len(candidates)}")
    print(f"B. ...and their neighbouring settings also worked: {len(robust)}")
    print(f"C. ...and still made money in the 12 hidden months: {len(survivors)}\n")
    for c in sorted(robust, key=lambda c: (not c["holdout_ok"], c["market"], -c["disc"]["t"])):
        print(("PASS " if c["holdout_ok"] else "fail ") + line(c))
    as_written(results)


if __name__ == "__main__":
    main(sys.argv[1:] or MARKETS)
