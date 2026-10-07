"""Replay the bot's own gold system code (src/strategies/gold_system.py) over 3
years, exactly as the live bot would trade it: one signal per closed 4-hour
candle, at most 600 candles of memory, per-trigger quiet periods, at most 2
open trades in one direction, nothing new in the last 2 hours before the
weekend, everything closed before the weekend, trades resolved on 5-minute
prices with the stop assumed first when both are touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research import gold_lab as lab  # noqa: E402
from scripts.research.classic import Plan  # noqa: E402
from scripts.research.gold_system import challenge, yearly  # noqa: E402
from scripts.research.robust2 import friday_cutoff  # noqa: E402
from src.domain.candle import Candle  # noqa: E402
from src.strategies import gold_system as system  # noqa: E402

LOOKBACK = 600


def replay() -> list[dict]:
    gold = lab.gold()
    h4 = lab.bars("4h")
    candles = [
        Candle("XAUUSD_H4", "H4", int(t.timestamp()) - 4 * 3600, float(r.o), float(r.h), float(r.l), float(r.c), 0.0)
        for t, r in h4.iterrows()
    ]
    ends = list(h4.index)
    last_fired: dict[str, int] = {}
    taken: list[dict] = []
    for k in range(250, len(candles) - 1):
        start = ends[k] + pd.Timedelta(minutes=5)
        cutoff = friday_cutoff(start, False)
        if start >= cutoff - pd.Timedelta(hours=2):
            continue
        window = candles[max(0, k + 1 - LOOKBACK):k + 1]
        setups = system.detect_all(window)
        if not setups:
            continue
        open_trades = [(t["component"], t["word"]) for t in taken if t["exit"] > start]
        ages = {s: (k - last_fired[s]) if s in last_fired else None for s in system.PLANS}
        chosen, fired, _ = system.choose(setups, open_trades, ages)
        for strategy in fired:
            last_fired[strategy] = k
        if chosen is None:
            continue
        d = 1 if chosen["trade_direction"] == "LONG" else -1
        deadline = min(ends[min(k + chosen["hold_candles"], len(ends) - 1)], cutoff)
        result = gold.run(Plan(start, d, chosen["entry_price"], chosen["sl_price"], chosen["tp_price"], deadline))
        if result:
            result.update(component=chosen["strategy"], word=chosen["trade_direction"])
            taken.append(result)
    return taken


if __name__ == "__main__":
    rows = replay()
    r = np.array([x["r"] for x in rows])
    print(f"gold system (bot code): {len(r)} trades ({len(r) / 3:.0f} a year), {100 * (r > 0).mean():.0f}% win, "
          f"{r.mean():+.3f}R avg, {r.sum():+.1f}R total")
    print("by year:", yearly(rows))
    for name in system.PLANS:
        print(f"  {name}: {yearly([x for x in rows if x['component'] == name])}")
    for word in ("LONG", "SHORT"):
        print(f"  {word}: {yearly([x for x in rows if x['word'] == word])}")
    total, peak, dip, streak, worst = 0.0, 0.0, 0.0, 0, 0
    for v in r:
        total += v
        peak = max(peak, total)
        dip = min(dip, total - peak)
        streak = streak + 1 if v < 0 else 0
        worst = max(worst, streak)
    print(f"worst losing run {worst}, deepest dip {dip:.1f}R")
    for risk in (0.5, 1.0, 1.5):
        chance, weeks = challenge(rows, risk)
        print(f"risk {risk}%: passes both phases {chance:.0f}% of the time, typically within {weeks:.0f} weeks")
