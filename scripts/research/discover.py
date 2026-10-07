"""Strategy discovery with an honest hold-out.

For every market x chart (1h, 4h, daily) x strategy family (scripts/research/families.py):

A. Discovery (first 24 months only): five stop/target plans. A candidate must have
   40+ trades, make money in each of the two years, t >= 2 and profit factor >= 1.15.
B. Robustness (still first 24 months): 27 neighbouring plans (stop, target, time
   limit each nudged). At least 60% must make money; otherwise it was a lucky peak.
C. Hold-out (last 12 months, never looked at before): the frozen plan must still
   make money there (t >= 1, profit factor >= 1.1).

Usage: python scripts/research/discover.py [MARKET ...]
Writes data/research/discovery.json (git-ignored) and prints the survivors.
"""

from __future__ import annotations

import json
import statistics
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research.engine import bars, market, periods, simulate  # noqa: E402
from scripts.research.families import FAMILIES, Ind  # noqa: E402

MARKETS = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
           "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "US100", "US500"]
HOLD = {"H1": 24, "H4": 30, "D1": 5}
PLANS = [(1.0, 1.0), (1.5, 1.0), (2.0, 0.5), (2.0, 1.0), (1.5, 2.0), (1.0, 2.0), (2.0, 3.0)]
OUT = ROOT / "data" / "research" / "discovery.json"


def discovery_ok(p: dict) -> bool:
    d = p["disc"]
    return d["n"] >= 40 and p["y1"]["avg"] > 0 and p["y2"]["avg"] > 0 and d["t"] >= 2.0 and d["pf"] >= 1.15


def holdout_ok(p: dict) -> bool:
    h = p["hold"]
    return h["n"] >= 8 and h["avg"] > 0 and h["t"] >= 1.0 and h["pf"] >= 1.1


def run_market(name: str) -> dict:
    try:
        data = market(name)
    except Exception as exc:  # missing history
        return {"market": name, "error": str(exc), "tested": 0, "candidates": []}
    tested, candidates = 0, []
    for tf in ("H1", "H4", "D1"):
        f = bars(data, tf)
        x = Ind(f)
        a = x.atr(14).to_numpy()
        for label, make, timeframes, style in FAMILIES:
            if tf not in timeframes:
                continue
            signal = make(x)
            if (signal != 0).sum() < 40:
                continue
            best = None
            for stop_atr, target_r in PLANS:
                tested += 1
                p = periods(simulate(data, f, signal, stop_atr * a, target_r, HOLD[tf]))
                if discovery_ok(p) and (best is None or p["disc"]["t"] > best[1]["disc"]["t"]):
                    best = ((stop_atr, target_r, HOLD[tf]), p)
            if best is None:
                continue
            (stop_atr, target_r, hold), p = best
            neighbours = []
            for s in (stop_atr * 0.75, stop_atr, stop_atr * 1.25):
                for g in (target_r * 0.75, target_r, target_r * 1.33):
                    for h in (max(2, round(hold * 0.6)), hold, round(hold * 1.6)):
                        q = periods(simulate(data, f, signal, s * a, g, h))
                        neighbours.append(q["disc"])
            share = sum(1 for q in neighbours if q["avg"] > 0) / len(neighbours)
            median_t = statistics.median(q["t"] for q in neighbours)
            candidates.append({
                "market": name, "tf": tf, "family": label, "style": style,
                "stop_atr": stop_atr, "target_r": target_r, "hold_bars": hold,
                "robust": share >= 0.6 and median_t >= 1.0, "share": round(share, 2), "median_t": round(median_t, 2),
                **p, "holdout_ok": holdout_ok(p),
            })
    return {"market": name, "tested": tested, "candidates": candidates}


def line(c: dict) -> str:
    d, h, a = c["disc"], c["hold"], c["all"]
    return (f"{c['market']:7s} {c['tf']} {c['family']:24s} stop {c['stop_atr']:.1f}ATR target {c['target_r']:.2f}R "
            f"hold {c['hold_bars']:2d} | 24m: {d['n']:3d} tr {d['win']:.0%} {d['avg']:+.3f}R t={d['t']:.1f} | "
            f"hold-out 12m: {h['n']:3d} tr {h['win']:.0%} {h['avg']:+.3f}R t={h['t']:.1f} pf={h['pf']:.2f} | "
            f"36m {a['total']:+.1f}R (neighbours {c['share']:.0%} ok)")


def main(names: list[str]) -> None:
    with Pool(3) as pool:
        results = pool.map(run_market, names)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1), encoding="utf-8")
    tested = sum(r["tested"] for r in results)
    candidates = [c for r in results for c in r["candidates"]]
    robust = [c for c in candidates if c["robust"]]
    survivors = [c for c in robust if c["holdout_ok"]]
    for r in results:
        if r.get("error"):
            print(f"{r['market']}: no history ({r['error']})")
    print(f"\nTested {tested} strategy x plan versions on {len(results)} markets.")
    print(f"A. passed the 24-month discovery test: {len(candidates)}")
    print(f"B. ...and their neighbouring settings also worked: {len(robust)}")
    print(f"C. ...and still made money in the 12 hidden months: {len(survivors)}\n")
    print("Robust candidates and how they did in the hidden 12 months:")
    for c in sorted(robust, key=lambda c: (not c["holdout_ok"], c["market"], -c["disc"]["t"])):
        print(("PASS " if c["holdout_ok"] else "fail ") + line(c))


if __name__ == "__main__":
    main(sys.argv[1:] or MARKETS)
