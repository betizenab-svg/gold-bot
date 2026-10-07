"""Breadth test: one strategy with the SAME settings on many markets at once.
An edge that holds across most pairs is far less likely to be a fluke than one
tuned to a single pair. Chosen on the first 24 months, checked on the hidden 12.

Usage: python scripts/research/breadth.py EURUSD GBPUSD USDJPY AUDUSD ...
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research.discover import HOLD, PLANS  # noqa: E402
from scripts.research.engine import YEAR1, YEAR2, bars, market, simulate, stats  # noqa: E402
from scripts.research.families import FAMILIES, Ind  # noqa: E402


def main(names: list[str]) -> None:
    charts = {}
    for name in names:
        try:
            data = market(name)
        except Exception as exc:
            print(f"{name}: skipped ({exc})")
            continue
        for tf in ("H1", "H4", "D1"):
            f = bars(data, tf)
            charts[(name, tf)] = (data, f, Ind(f))
    markets = sorted({name for name, _ in charts})
    found = []
    for label, make, timeframes, _style in FAMILIES:
        for tf in timeframes:
            signals = {m: make(charts[(m, tf)][2]) for m in markets}
            for stop_atr, target_r in PLANS:
                per_market, pooled = {}, []
                for m in markets:
                    data, f, x = charts[(m, tf)]
                    rows = simulate(data, f, signals[m], stop_atr * x.atr(14).to_numpy(), target_r, HOLD[tf])
                    per_market[m] = rows
                    pooled.append(rows)
                allrows = np.vstack(pooled) if pooled else np.zeros((0, 4))
                t, r = allrows[:, 0], allrows[:, 3]
                disc, y1, y2, hidden = stats(r[t < YEAR2]), stats(r[t < YEAR1]), stats(r[(t >= YEAR1) & (t < YEAR2)]), stats(r[t >= YEAR2])
                positive = sum(1 for m in markets if stats(per_market[m][per_market[m][:, 0] < YEAR2, 3])["avg"] > 0)
                if disc["n"] >= 80 and y1["avg"] > 0 and y2["avg"] > 0 and disc["t"] >= 2.5 and positive >= 0.75 * len(markets):
                    hidden_positive = sum(1 for m in markets if stats(per_market[m][per_market[m][:, 0] >= YEAR2, 3])["avg"] > 0)
                    found.append((label, tf, stop_atr, target_r, disc, hidden, positive, hidden_positive))
    print(f"Markets: {', '.join(markets)}. Same settings everywhere; discovery needs t >= 2.5 pooled and 75% of markets positive.")
    for label, tf, stop_atr, target_r, disc, hidden, positive, hidden_positive in sorted(found, key=lambda x: -x[4]["t"]):
        verdict = "PASS" if hidden["avg"] > 0 and hidden["t"] >= 1.5 else "fail"
        print(f"{verdict} {label:24s} {tf} stop {stop_atr}ATR target {target_r}R | 24m pooled {disc['n']} tr "
              f"{disc['avg']:+.3f}R t={disc['t']:.1f} ({positive}/{len(markets)} markets up) | hidden 12m {hidden['n']} tr "
              f"{hidden['avg']:+.3f}R t={hidden['t']:.1f} ({hidden_positive}/{len(markets)} up)")
    if not found:
        print("Nothing passed the pooled discovery test.")


if __name__ == "__main__":
    main(sys.argv[1:])
