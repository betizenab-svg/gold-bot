"""Can more markets make the Alpha challenge faster without lowering quality?

1. The Gold 4-hour System's exact triggers and plans, replayed on every other
   market with history (FX majors, silver, US indices), same rules as live.
2. Connors RSI(2) dip-buying on the US indices in the bot's trade form
   (fixed stop and target, time limit, closed before the weekend).
3. A portfolio of the survivors on the Alpha Pro 8% challenge, walked through
   the real 3-year sequence from every possible start day.

Usage: python scripts/research/portfolio.py
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research.classic import Market, Plan, resample  # noqa: E402
from scripts.research.robust import YEARS, report, run_all  # noqa: E402
from scripts.research.robust2 import friday_cutoff, rsi2_fixed, weekend_safe  # noqa: E402
from src.domain.candle import Candle  # noqa: E402
from src.strategies import gold_system as system  # noqa: E402

H4 = 4 * 3600
LOOKBACK = 600
INDICES = {"US100", "US500"}
_MARKETS: dict[str, Market] = {}


def market(name: str) -> Market:
    if name not in _MARKETS:
        _MARKETS[name] = Market(name)
    return _MARKETS[name]


def setups_at(window: list[Candle]) -> list[dict]:
    out = []
    for strategy, trigger in system.TRIGGERS:
        direction = trigger(window)
        if direction:
            setup = system.build_setup(window, strategy, direction)
            if setup is not None:
                out.append(setup)
    return out


def choose(setups, open_trades, ages, max_same: int, stack: bool):
    chosen, fired = None, []
    for setup in setups:
        strategy, direction = setup["strategy"], setup["trade_direction"]
        age = ages.get(strategy)
        if age is not None and age < system.PLANS[strategy].quiet_candles:
            continue
        fired.append(strategy)
        same = [s for s, d in open_trades if d == direction]
        if chosen is None and (stack or strategy not in same) and len(same) < max_same:
            chosen = setup
    return chosen, fired


def system_trades(name: str, max_same: int = 2, stack: bool = False) -> list[dict]:
    """The live gold system's code on any market, resolved on 5-minute prices."""
    m = market(name)
    h4 = resample(m.m5, "4h")
    candles = [
        Candle(name, "H4", int(t.timestamp()) - H4, float(r.o), float(r.h), float(r.l), float(r.c), 0.0)
        for t, r in h4.iterrows()
    ]
    ends = list(h4.index)
    last_fired: dict[str, int] = {}
    taken: list[dict] = []
    index_market = name in INDICES
    for k in range(250, len(candles) - 1):
        start = ends[k] + pd.Timedelta(minutes=5)
        cutoff = friday_cutoff(start, index_market)
        if start >= cutoff - pd.Timedelta(hours=2):
            continue
        setups = setups_at(candles[max(0, k + 1 - LOOKBACK):k + 1])
        if not setups:
            continue
        open_trades = [(t["component"], t["word"]) for t in taken if t["exit"] > start]
        ages = {s: (k - last_fired[s]) if s in last_fired else None for s in system.PLANS}
        chosen, fired = choose(setups, open_trades, ages, max_same, stack)
        for strategy in fired:
            last_fired[strategy] = k
        if chosen is None:
            continue
        d = 1 if chosen["trade_direction"] == "LONG" else -1
        deadline = min(ends[min(k + chosen["hold_candles"], len(ends) - 1)], cutoff)
        result = m.run(Plan(start, d, chosen["entry_price"], chosen["sl_price"], chosen["tp_price"], deadline))
        if result:
            result.update(component=chosen["strategy"], word=chosen["trade_direction"], market=name)
            taken.append(result)
    return taken


def rsi2_trades(name: str, threshold: float, stop_atr: float, target_atr: float, max_days: int) -> list[dict]:
    m = market(name)
    rows = run_all(m, weekend_safe(rsi2_fixed(m, threshold, stop_atr, target_atr, max_days), True))
    for row in rows:
        row.update(component="RSI2", word="LONG", market=name)
    return rows


def yearly(rows: list[dict]) -> str:
    parts = []
    for label, a, b in YEARS:
        sel = [x["r"] for x in rows if pd.Timestamp(a, tz="UTC") <= x["time"] < pd.Timestamp(b, tz="UTC")]
        parts.append(f"{label} {len(sel)} {sum(sel):+.1f}R" if sel else f"{label} -")
    return " | ".join(parts)


# --- challenge -----------------------------------------------------------------------------

def daily_pnl(rows: list[dict]) -> tuple[list[pd.Timestamp], list[tuple[float, float]]]:
    """Per business day: (day's total R, day's losses only), by exit day. Losses
    are assumed to come before the day's wins (the daily-loss rule is checked on them)."""
    by_day: dict[pd.Timestamp, list[float]] = defaultdict(list)
    for x in rows:
        by_day[x["exit"].normalize()].append(x["r"])
    first = min(x["time"] for x in rows).normalize()
    last = max(x["exit"] for x in rows).normalize()
    days = list(pd.bdate_range(first, last, tz="UTC"))
    out = []
    for day in days:
        values = by_day.get(day, [])
        out.append((sum(values), sum(v for v in values if v < 0)))
    return days, out


def challenge(rows: list[dict], risk: float) -> tuple[float, float, float]:
    """Walk the real day-by-day sequence from every start day (wrapping around):
    phase 1 +8%, then phase 2 +5%; fail at -8% total or -4% in a day.
    Returns (pass %, median weeks, 80th-percentile weeks) for passes."""
    days, pnl = daily_pnl(rows)
    n = len(pnl)
    passed, weeks = 0, []

    def phase(start: int, target: float) -> tuple[Optional[bool], int]:
        balance = 0.0
        for step in range(n):
            total, losses = pnl[(start + step) % n]
            if risk * losses <= -4.0 or balance + risk * losses <= -8.0:
                return False, step + 1
            balance += risk * total
            if balance <= -8.0:
                return False, step + 1
            if balance >= target:
                return True, step + 1
        return None, n

    for start in range(n):
        ok1, used1 = phase(start, 8.0)
        if not ok1:
            continue
        ok2, used2 = phase((start + used1) % n, 5.0)
        if ok2:
            passed += 1
            weeks.append((used1 + used2) / 5.0)
    if not weeks:
        return 0.0, float("nan"), float("nan")
    return 100.0 * passed / n, statistics.median(weeks), float(np.percentile(weeks, 80))


def portfolio(streams: list[list[dict]], max_open: int) -> list[dict]:
    """Merge trade lists in time order, skipping a trade while `max_open` trades are open."""
    merged = sorted((x for stream in streams for x in stream), key=lambda x: x["time"])
    taken: list[dict] = []
    for x in merged:
        if sum(1 for t in taken if t["exit"] > x["time"]) >= max_open:
            continue
        taken.append(x)
    return taken


def show(label: str, rows: list[dict]) -> None:
    r = np.array([x["r"] for x in rows])
    print(f"\n=== {label}: {len(r)} trades ({len(r) / 3:.0f}/yr), {100 * (r > 0).mean():.0f}% win, "
          f"{r.mean():+.3f}R avg, {r.sum():+.1f}R | {yearly(rows)}", flush=True)
    for risk in (0.75, 1.0, 1.25, 1.5):
        chance, median_weeks, slow_weeks = challenge(rows, risk)
        print(f"   risk {risk}%: passes {chance:.0f}% of start days, median {median_weeks:.0f} weeks "
              f"(slowest 20%: {slow_weeks:.0f}+)", flush=True)


def main() -> None:
    survivors: dict[str, list[dict]] = {}
    print("# 1. The gold system's exact code on other markets (weekend-safe, 5-minute resolution)")
    for name in ("XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "US100", "US500"):
        rows = system_trades(name)
        ok, line = report(f"{name} system", rows)
        print(line, "|", yearly(rows), flush=True)
        for component in system.PLANS:
            sub = [x for x in rows if x["component"] == component]
            print(f"      {component}: {yearly(sub)}", flush=True)
        if ok:
            survivors[f"{name} system"] = rows

    print("\n# 2. RSI(2) dip-buying on US indices (fixed plan, weekend-safe); canonical first")
    grid: list[tuple[float, float, float, int]] = [(10, 3.0, 0.75, 5)] + [
        (t, s, g, d) for t in (5, 10, 15) for s in (2.0, 3.0) for g in (0.5, 0.75, 1.0) for d in (3, 5)
    ]
    for name in sorted(INDICES):
        passes = 0
        for threshold, stop, target, days in grid:
            rows = rsi2_trades(name, threshold, stop, target, days)
            ok, line = report(f"{name} rsi2<{threshold} stop{stop} target{target} max{days}d", rows)
            passes += ok
            if (threshold, stop, target, days) == grid[0]:
                print(line, "|", yearly(rows), flush=True)
                if ok:
                    survivors[f"{name} RSI2"] = rows
        print(f"   {name}: {passes} of {len(grid)} RSI2 settings pass (neighbourhood check)", flush=True)

    print("\n# 3. Gold system rule variants (stacking)")
    for max_same, stack in ((2, True), (3, False), (3, True)):
        rows = system_trades("XAUUSD", max_same, stack)
        ok, line = report(f"XAUUSD system max_same={max_same} stack={stack}", rows)
        print(line, "|", yearly(rows), flush=True)
        survivors[f"XAUUSD system max{max_same} stack{stack}"] = rows

    print("\n# 4. Challenge: gold alone vs portfolios of survivors")
    gold = survivors.get("XAUUSD system") or system_trades("XAUUSD")
    show("gold system (live rules)", gold)
    extras = {k: v for k, v in survivors.items() if not k.startswith("XAUUSD")}
    for label, rows in extras.items():
        show(f"gold + {label}", portfolio([gold, rows], max_open=4))
    if len(extras) > 1:
        show("gold + all survivors", portfolio([gold, *extras.values()], max_open=4))
    for label, rows in survivors.items():
        if label.startswith("XAUUSD system max"):
            show(label, rows)


if __name__ == "__main__":
    main()
