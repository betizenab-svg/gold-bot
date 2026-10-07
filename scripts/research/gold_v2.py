"""Gold System v2 candidates: combine the gold strategies that survived the
hidden-year test (scripts/research/discover.py) and compare them with the
live system (v1) on the same engine, choosing on the first 24 months only.

Usage: python scripts/research/gold_v2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research import families as fam  # noqa: E402
from scripts.research.engine import YEAR1, YEAR2, bars, market, simulate, stats  # noqa: E402
from scripts.research.sizing import ladder, walk  # noqa: E402

# name: (chart, signal maker, stop ATR, target R, time limit in bars)
COMPONENTS = {
    "v1_breakout": ("H4", lambda x: fam.donchian(x, 30, False), 1.5, 1.0, 30),
    "v1_squeeze": ("H4", lambda x: fam.squeeze(x, 60, False), 1.5, 1.0, 30),
    "v1_pullback": ("H4", lambda x: fam.ema_pullback(x, 20, 50, 200), 2.0, 1.0, 24),
    "donchian20_trend": ("H4", lambda x: fam.donchian(x, 20, True), 1.5, 2.0, 30),
    "keltner1.5_trend": ("H4", lambda x: fam.keltner(x, 1.5, True), 1.5, 2.0, 30),
    "keltner2.5_trend": ("H4", lambda x: fam.keltner(x, 2.5, True), 1.5, 2.0, 30),
    "nr4_trend": ("H4", lambda x: fam.nr_break(x, 4, True), 2.0, 3.0, 30),
    "inside_trend": ("H4", lambda x: fam.inside_break(x, True), 1.0, 1.0, 30),
    "cci_trend_h4": ("H4", fam.cci_break, 2.0, 1.0, 30),
    "ichimoku_h4": ("H4", fam.ichimoku, 2.0, 1.0, 30),
    "adx20_h1": ("H1", lambda x: fam.adx_break(x, 20), 2.0, 3.0, 24),
    "donchian20_trend_d1": ("D1", lambda x: fam.donchian(x, 20, True), 1.5, 1.0, 5),
}
SETS = {
    "v1 (live)": ["v1_breakout", "v1_squeeze", "v1_pullback"],
    "v2 core (4h: breakout, keltner, nr4, inside)": ["donchian20_trend", "keltner1.5_trend", "nr4_trend", "inside_trend"],
    "v2 4h wide (7 triggers)": ["donchian20_trend", "keltner1.5_trend", "keltner2.5_trend", "nr4_trend", "inside_trend",
                                "cci_trend_h4", "ichimoku_h4"],
    "v2 core + 1h adx + daily breakout": ["donchian20_trend", "keltner1.5_trend", "nr4_trend", "inside_trend", "adx20_h1",
                                          "donchian20_trend_d1"],
    "v1 + v2 core": ["v1_breakout", "v1_squeeze", "v1_pullback", "donchian20_trend", "keltner1.5_trend", "nr4_trend",
                     "inside_trend"],
}


def component_trades(name: str) -> list[dict]:
    chart, make, stop_atr, target_r, hold = COMPONENTS[name]
    data = market("XAUUSD")
    f = bars(data, chart)
    x = fam.Ind(f)
    rows = simulate(data, f, make(x), stop_atr * x.atr(14).to_numpy(), target_r, hold)
    return [
        {"time": pd.Timestamp(int(t0), unit="s", tz="UTC"), "exit": pd.Timestamp(int(t1), unit="s", tz="UTC"),
         "direction": int(d), "r": float(r), "component": name}
        for t0, t1, d, r in rows
    ]


def combine(names: list[str], max_same: int) -> list[dict]:
    merged = sorted((x for name in names for x in component_trades(name)), key=lambda x: x["time"])
    taken: list[dict] = []
    for x in merged:
        same = sum(1 for t in taken if t["exit"] > x["time"] and t["direction"] == x["direction"])
        if same < max_same:
            taken.append(x)
    return taken


def describe(rows: list[dict]) -> str:
    t = np.array([x["time"].timestamp() for x in rows])
    r = np.array([x["r"] for x in rows])
    parts = []
    for label, sel in (("Y1", t < YEAR1), ("Y2", (t >= YEAR1) & (t < YEAR2)), ("hidden Y3", t >= YEAR2)):
        s = stats(r[sel])
        parts.append(f"{label} {s['n']} tr {s['avg']:+.2f}R {s['total']:+.1f}R")
    s = stats(r)
    return f"{s['n']} trades ({s['n'] / 3:.0f}/yr) {s['win']:.0%} win {s['avg']:+.3f}R avg {s['total']:+.1f}R t={s['t']:.1f} | " + " | ".join(parts)


def main() -> None:
    """Fair comparison: for each set, the biggest ladder size that still passes the
    challenge from at least 97% of start days in the FIRST 24 MONTHS, and how fast
    it is there; then the same size on the hidden 12 months."""
    for label, names in SETS.items():
        for max_same in (1, 2):
            rows = combine(names, max_same)
            disc = [x for x in rows if x["time"].timestamp() < YEAR2]
            hidden = [x for x in rows if x["time"].timestamp() >= YEAR2]
            best = None
            for scale in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4):
                chance, weeks, _ = walk(disc, ladder(1.5 * scale, 1.0 * scale, 0.5 * scale))
                if chance >= 97.0:
                    best = (scale, weeks)
            print(f"\n=== {label}, max {max_same} same direction\n   {describe(rows)}")
            if best is None:
                print("   no ladder size passes 97% of start days in the first 24 months", flush=True)
                continue
            scale, weeks = best
            rule = ladder(1.5 * scale, 1.0 * scale, 0.5 * scale)
            h_chance, h_weeks, _ = walk(hidden, rule) if len(hidden) > 20 else (float("nan"),) * 3
            print(f"   safe size: top {1.5 * scale:.2f}% (ladder x{scale}) -> first 24m median {weeks:.0f} weeks | "
                  f"hidden 12m at that size: {h_chance:.0f}% pass, median {h_weeks:.0f} weeks", flush=True)


if __name__ == "__main__":
    main()
