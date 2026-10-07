"""Robustness checks for the classic strategies that passed both periods.

A candidate is only trusted if it is positive in each of the 3 years, its
neighbouring settings also work, and the whole 3 years is clearly above zero
(t-statistic). FX entries in the daily rollover hours pay triple spread.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research.classic import Market, Plan, atr, donchian, nr7, resample, rsi  # noqa: E402

YEARS = [("Y1", "2023-10-01", "2024-10-01"), ("Y2", "2024-10-01", "2025-10-01"), ("Y3", "2025-10-01", "2026-10-01")]
ROLLOVER_HOURS = {21, 22}


def run_all(market: Market, plans: Iterable[Plan], rollover: bool = False) -> list[dict]:
    out = []
    base_cost = market.cost
    for plan in plans:
        if rollover and plan.start.hour in ROLLOVER_HOURS:
            market.cost = base_cost * 3.0
        res = market.run(plan)
        market.cost = base_cost
        if res:
            out.append(res)
    return out


def report(label: str, results: list[dict]) -> tuple[bool, str]:
    r = np.array([x["r"] for x in results])
    if len(r) < 20:
        return False, f"{label}: only {len(r)} trades"
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if r.std(ddof=1) > 0 else 0.0
    parts, every_year = [], True
    for name, a, b in YEARS:
        sel = np.array([x["r"] for x in results if pd.Timestamp(a, tz="UTC") <= x["time"] < pd.Timestamp(b, tz="UTC")])
        if len(sel) == 0:
            every_year = False
            parts.append(f"{name} -")
            continue
        every_year &= sel.mean() > 0
        parts.append(f"{name} {len(sel)} {100 * (sel > 0).mean():.0f}% {sel.mean():+.2f}")
    ok = every_year and t >= 2.0
    return ok, (f"{'PASS' if ok else '    '} {label}: n={len(r)} win={100 * (r > 0).mean():.0f}% avg={r.mean():+.3f} "
                f"t={t:.1f} | " + " | ".join(parts))


def asia_fade(m: Market, sigma: float, stop_atr: float, hours: set[int], max_hours: int = 12) -> Iterable[Plan]:
    h1 = resample(m.m5, "1h")
    mid, sd, a = h1["c"].rolling(20).mean(), h1["c"].rolling(20).std(), atr(h1, 14)
    idx, close = h1.index, h1["c"].to_numpy()
    for k in range(30, len(h1) - max_hours - 1):
        if idx[k].hour not in hours:
            continue
        c = close[k]
        d = 1 if c < mid.iloc[k] - sigma * sd.iloc[k] else -1 if c > mid.iloc[k] + sigma * sd.iloc[k] else 0
        if d == 0 or not np.isfinite(a.iloc[k]):
            continue
        yield Plan(idx[k] + pd.Timedelta(minutes=5), d, c, c - d * stop_atr * a.iloc[k], mid.iloc[k], idx[k + max_hours])


def rsi2_variant(m: Market, threshold: float, exit_rule: str, stop_atr: float | None, max_days: int = 10) -> Iterable[Plan]:
    d1 = m.d1
    close, high = d1["c"], d1["h"]
    r2, sma200, sma5, a = rsi(close, 2), close.rolling(200).mean(), close.rolling(5).mean(), atr(d1, 10)
    k = 200
    while k < len(d1) - 1:
        if not (close.iloc[k] > sma200.iloc[k] and r2.iloc[k] < threshold):
            k += 1
            continue
        j = k + 1
        while j < min(k + max_days, len(d1) - 1):
            if exit_rule == "sma5" and close.iloc[j] > sma5.iloc[j]:
                break
            if exit_rule == "prev_high" and close.iloc[j] > high.iloc[j - 1]:
                break
            if exit_rule == "rsi70" and rsi(close.iloc[: j + 1], 2).iloc[-1] > 70:
                break
            j += 1
        stop = close.iloc[k] - (stop_atr * a.iloc[k] if stop_atr else 0.25 * close.iloc[k])
        yield Plan(d1["last_ts"].iloc[k] + pd.Timedelta(minutes=5), 1, close.iloc[k], stop, None, d1["last_ts"].iloc[j])
        k = j + 1


def donchian_fixed(m: Market, rule: str, n: int, target_r: float) -> Iterable[Plan]:
    """Channel breakout with a fixed 2-ATR stop and fixed target (fits the bot's TP model)."""
    bars = resample(m.m5, rule)
    a = atr(bars, 20)
    upper, lower = bars["h"].rolling(n).max().shift(1), bars["l"].rolling(n).min().shift(1)
    times, close = bars.index, bars["c"].to_numpy()
    k, horizon = n + 25, 30
    while k < len(bars) - horizon - 1:
        d = 1 if close[k] > upper.iloc[k] else -1 if close[k] < lower.iloc[k] else 0
        if d == 0 or not np.isfinite(a.iloc[k]):
            k += 1
            continue
        stop = close[k] - d * 2.0 * a.iloc[k]
        target = close[k] + d * target_r * 2.0 * a.iloc[k]
        yield Plan(times[k] + pd.Timedelta(minutes=5), d, close[k], stop, target, times[k + horizon])
        k += horizon // 3


FX = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "XAUUSD", "XAGUSD", "US100", "US500"]


def main() -> None:
    markets = {name: Market(name) for name in FX}
    print("# Quiet-Asian-session fade (entry hours UTC; rollover hours pay 3x spread)")
    windows = {"22-05": {22, 23, 0, 1, 2, 3, 4}, "23-05": {23, 0, 1, 2, 3, 4}, "00-05": {0, 1, 2, 3, 4}}
    for name, m in markets.items():
        for wname, hours in windows.items():
            for sigma in (1.8, 2.0, 2.2, 2.5):
                for stop in (1.0, 1.5, 2.0):
                    ok, line = report(f"{name} fade {wname} sigma{sigma} stop{stop}", run_all(m, asia_fade(m, sigma, stop, hours), rollover=True))
                    if ok or (sigma == 2.0 and stop == 1.5):
                        print(line, flush=True)
    print("\n# RSI(2) pullback longs (daily)")
    for name in ("US500", "US100", "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "AUDUSD", "USDJPY"):
        m = markets[name]
        for threshold in (5, 10, 15, 20, 25):
            for exit_rule in ("sma5", "prev_high"):
                for stop in (None, 3.0):
                    ok, line = report(f"{name} rsi2<{threshold} exit={exit_rule} stop={stop}", run_all(m, rsi2_variant(m, threshold, exit_rule, stop)))
                    if ok or (threshold == 10 and exit_rule == "sma5" and stop is None):
                        print(line, flush=True)
    print("\n# Trend breakout with fixed targets (fits the bot's take-profit model)")
    for name in ("XAUUSD", "XAGUSD", "US100", "US500", "EURUSD", "GBPUSD", "AUDUSD", "USDJPY"):
        m = markets[name]
        for rule in ("1h", "4h"):
            for n in (20, 40):
                for target in (1.0, 1.5, 2.0):
                    ok, line = report(f"{name} donchian {rule} {n} target {target}R", run_all(m, donchian_fixed(m, rule, n, target)))
                    if ok:
                        print(line, flush=True)
        ok, line = report(f"{name} donchian 4h 20/10 trailing", run_all(m, donchian(m, "4h", 20, 10)))
        print(line, flush=True)
        ok, line = report(f"{name} NR7 to close", run_all(m, nr7(m, None)))
        print(line, flush=True)


if __name__ == "__main__":
    main()
