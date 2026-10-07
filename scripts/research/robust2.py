"""Second robustness pass for the two surviving families, in the exact form the
bot can trade: one target, one stop, a time limit, and closed before the weekend.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research.classic import NY, Market, Plan, atr, resample, rsi  # noqa: E402
from scripts.research.robust import YEARS, report, run_all  # noqa: E402


def friday_cutoff(moment: pd.Timestamp, index_market: bool) -> pd.Timestamp:
    """Last moment to hold before the weekend (FX/gold: 20:30 UTC Friday; US cash indices: 15:55 New York)."""
    local = moment.tz_convert(NY) if index_market else moment
    days_ahead = (4 - local.weekday()) % 7
    friday = (local + pd.Timedelta(days=days_ahead)).normalize()
    if index_market:
        cutoff = (friday + pd.Timedelta(hours=15, minutes=55)).tz_convert("UTC")
    else:
        cutoff = friday + pd.Timedelta(hours=20, minutes=30)
    if cutoff <= moment:
        cutoff += pd.Timedelta(days=7)
    return cutoff


def weekend_safe(plans: Iterable[Plan], index_market: bool) -> Iterable[Plan]:
    for plan in plans:
        cutoff = friday_cutoff(plan.start, index_market)
        if plan.start >= cutoff - pd.Timedelta(hours=2):
            continue  # the bot opens nothing in the last two hours before the weekend
        yield Plan(plan.start, plan.direction, plan.entry, plan.stop, plan.target, min(plan.deadline, cutoff),
                   plan.stop_entry, plan.expire)


def trend_breakout(m: Market, n: int, stop_atr: float, target_r: float, horizon: int, side: int = 0) -> Iterable[Plan]:
    bars = resample(m.m5, "4h")
    a = atr(bars, 20)
    upper, lower = bars["h"].rolling(n).max().shift(1), bars["l"].rolling(n).min().shift(1)
    times, close = bars.index, bars["c"].to_numpy()
    k = n + 25
    while k < len(bars) - horizon - 1:
        d = 1 if close[k] > upper.iloc[k] else -1 if close[k] < lower.iloc[k] else 0
        if d == 0 or (side and d != side) or not np.isfinite(a.iloc[k]):
            k += 1
            continue
        risk = stop_atr * a.iloc[k]
        yield Plan(times[k] + pd.Timedelta(minutes=5), d, close[k], close[k] - d * risk, close[k] + d * target_r * risk,
                   times[k + horizon])
        k += max(1, horizon // 3)


def rsi2_fixed(m: Market, threshold: float, stop_atr: float, target_atr: float, max_days: int) -> Iterable[Plan]:
    d1 = m.d1
    close = d1["c"]
    r2, sma200, a = rsi(close, 2), close.rolling(200).mean(), atr(d1, 10)
    k = 200
    while k < len(d1) - max_days - 1:
        if close.iloc[k] > sma200.iloc[k] and r2.iloc[k] < threshold and np.isfinite(a.iloc[k]):
            entry = close.iloc[k]
            yield Plan(d1["last_ts"].iloc[k] + pd.Timedelta(minutes=5), 1, entry, entry - stop_atr * a.iloc[k],
                       entry + target_atr * a.iloc[k], d1["last_ts"].iloc[k + max_days])
            k += 2
        else:
            k += 1


def extra(results: list[dict]) -> str:
    r = [x["r"] for x in sorted(results, key=lambda x: x["time"])]
    if not r:
        return ""
    total, peak, dip, streak, worst = 0.0, 0.0, 0.0, 0, 0
    for v in r:
        total += v
        peak = max(peak, total)
        dip = min(dip, total - peak)
        streak = streak + 1 if v < 0 else 0
        worst = max(worst, streak)
    first = results[0]["time"] if results else None
    span_years = max(0.5, (YEARS[-1][2] and (pd.Timestamp(YEARS[-1][2], tz="UTC") - min(x["time"] for x in results)).days / 365.25))
    return f" | {len(r) / span_years:.0f}/yr, worst losing run {worst}, deepest dip {dip:.1f}R" + ("" if first is None else "")


def main() -> None:
    gold = Market("XAUUSD")
    print("# Gold trend breakout on 4-hour bars: single target, time limit, closed before weekends")
    for n in (20, 30, 40):
        for stop in (1.5, 2.0, 2.5):
            for target in (0.75, 1.0, 1.5):
                for horizon in (18, 30):
                    plans = weekend_safe(trend_breakout(gold, n, stop, target, horizon), False)
                    ok, line = report(f"gold n{n} stop{stop}ATR target{target}R hold{horizon}x4h", res := run_all(gold, plans))
                    if ok:
                        print(line + extra(res), flush=True)
    print("\n# Same, longs only vs shorts only (n20 stop2.0 target1.0 hold30, weekend-safe)")
    for side, label in ((1, "longs"), (-1, "shorts")):
        ok, line = report(f"gold {label}", res := run_all(gold, weekend_safe(trend_breakout(gold, 20, 2.0, 1.0, 30, side), False)))
        print(line + extra(res), flush=True)
    print("\n# Without the weekend close, for comparison")
    ok, line = report("gold n20 stop2.0 target1.0 hold30 (holds weekends)", res := run_all(gold, trend_breakout(gold, 20, 2.0, 1.0, 30)))
    print(line + extra(res), flush=True)
    for name in ("XAGUSD", "US100", "US500", "EURUSD", "GBPUSD", "AUDUSD", "USDJPY"):
        m = Market(name)
        ok, line = report(f"{name} trend n20 stop2.0 target1.0 hold30 (weekend-safe)",
                          res := run_all(m, weekend_safe(trend_breakout(m, 20, 2.0, 1.0, 30), name.startswith("US") and len(name) == 5)))
        print(line + extra(res), flush=True)

    print("\n# RSI(2) pullback on US indices: fixed target and stop, closed before weekends")
    for name in ("US500", "US100"):
        m = Market(name)
        for threshold in (10, 15, 20):
            for stop in (2.0, 3.0):
                for target in (0.5, 0.75, 1.0):
                    for max_days in (3, 5):
                        plans = weekend_safe(rsi2_fixed(m, threshold, stop, target, max_days), True)
                        ok, line = report(f"{name} rsi2<{threshold} stop{stop}ATR target{target}ATR max{max_days}d", res := run_all(m, plans))
                        if ok or (threshold == 10 and stop == 3.0 and target == 0.75 and max_days == 5):
                            print(line + extra(res), flush=True)


if __name__ == "__main__":
    main()
