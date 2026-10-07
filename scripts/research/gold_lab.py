"""Gold strategy lab: well-known strategy families, in the form the bot can trade
(market entry after a closed candle, stop, one target, time limit, closed before
the weekend), judged after costs on each of the 3 years.

Run: .venv/bin/python scripts/research/gold_lab.py [family ...]
"""

from __future__ import annotations

import functools
import itertools
import sys
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research.classic import NY, Market, Plan, atr, resample, rsi  # noqa: E402
from scripts.research.robust import report, run_all  # noqa: E402
from scripts.research.robust2 import weekend_safe  # noqa: E402

GOLD = None


def gold() -> Market:
    global GOLD
    if GOLD is None:
        GOLD = Market("XAUUSD")
    return GOLD


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


@functools.lru_cache(maxsize=None)
def bars(rule: str) -> pd.DataFrame:
    frame = resample(gold().m5, rule)
    frame["atr"] = atr(frame, 14)
    frame["ema20"], frame["ema50"], frame["ema200"] = ema(frame["c"], 20), ema(frame["c"], 50), ema(frame["c"], 200)
    return frame


def plan_at(frame: pd.DataFrame, k: int, d: int, stop_atr: float, target_r: float, hold: int) -> Plan:
    c, a = frame["c"].iloc[k], frame["atr"].iloc[k]
    risk = stop_atr * a
    end = frame.index[min(k + hold, len(frame) - 1)]
    return Plan(frame.index[k] + pd.Timedelta(minutes=5), d, c, c - d * risk, c + d * target_r * risk, end)


def trend_ok(frame: pd.DataFrame, k: int, d: int, mode: str) -> bool:
    if mode == "none":
        return True
    if mode == "ema200":
        return d * (frame["c"].iloc[k] - frame["ema200"].iloc[k]) > 0
    if mode == "ema50>200":
        return d * (frame["ema50"].iloc[k] - frame["ema200"].iloc[k]) > 0
    raise ValueError(mode)


# --- families ---------------------------------------------------------------------------------

def donchian_break(rule: str, n: int, stop_atr: float, target_r: float, hold: int, trend: str) -> Iterable[Plan]:
    """Channel breakout (Turtle/Donchian), trend-filtered."""
    f = bars(rule)
    upper, lower = f["h"].rolling(n).max().shift(1), f["l"].rolling(n).min().shift(1)
    k, gap = max(n, 210), max(1, hold // 3)
    while k < len(f) - 1:
        c = f["c"].iloc[k]
        d = 1 if c > upper.iloc[k] else -1 if c < lower.iloc[k] else 0
        if d and np.isfinite(f["atr"].iloc[k]) and trend_ok(f, k, d, trend):
            yield plan_at(f, k, d, stop_atr, target_r, hold)
            k += gap
        else:
            k += 1


def trend_pullback_rsi(rule: str, rsi_n: int, level: float, stop_atr: float, target_r: float, hold: int, trend: str) -> Iterable[Plan]:
    """Buy short-term oversold in an uptrend / sell overbought in a downtrend (Connors-style)."""
    f = bars(rule)
    r = rsi(f["c"], rsi_n)
    k = 210
    while k < len(f) - 1:
        d = 0
        if r.iloc[k] < level and trend_ok(f, k, 1, trend):
            d = 1
        elif r.iloc[k] > 100 - level and trend_ok(f, k, -1, trend):
            d = -1
        if d and np.isfinite(f["atr"].iloc[k]):
            yield plan_at(f, k, d, stop_atr, target_r, hold)
            k += 3
        else:
            k += 1


def ema_pullback(rule: str, stop_atr: float, target_r: float, hold: int) -> Iterable[Plan]:
    """Strong trend (EMA20 > EMA50 > EMA200); price dips to the EMA20 and closes back in the trend direction."""
    f = bars(rule)
    k = 210
    while k < len(f) - 1:
        e20, e50, e200 = f["ema20"].iloc[k], f["ema50"].iloc[k], f["ema200"].iloc[k]
        o, h, low, c = f["o"].iloc[k], f["h"].iloc[k], f["l"].iloc[k], f["c"].iloc[k]
        d = 0
        if e20 > e50 > e200 and low <= e20 and c > e20 and c > o:
            d = 1
        elif e20 < e50 < e200 and h >= e20 and c < e20 and c < o:
            d = -1
        if d and np.isfinite(f["atr"].iloc[k]):
            yield plan_at(f, k, d, stop_atr, target_r, hold)
            k += 3
        else:
            k += 1


def squeeze_break(rule: str, look: int, stop_atr: float, target_r: float, hold: int, trend: str) -> Iterable[Plan]:
    """Bollinger squeeze: bandwidth at its lowest of `look` bars, then a close outside the band."""
    f = bars(rule)
    mid, sd = f["c"].rolling(20).mean(), f["c"].rolling(20).std()
    width = (4 * sd) / mid
    squeezed = width <= width.rolling(look).min() * 1.05
    k = max(210, look)
    while k < len(f) - 1:
        if squeezed.iloc[k - 1:k + 1].any():
            c = f["c"].iloc[k]
            d = 1 if c > mid.iloc[k] + 2 * sd.iloc[k] else -1 if c < mid.iloc[k] - 2 * sd.iloc[k] else 0
            if d and trend_ok(f, k, d, trend) and np.isfinite(f["atr"].iloc[k]):
                yield plan_at(f, k, d, stop_atr, target_r, hold)
                k += 6
                continue
        k += 1


def session_breakout(range_start: int, range_end: int, window_end: int, target_r: float, trend: str) -> Iterable[Plan]:
    """Range of the quiet hours (UTC), first 15-minute close beyond it before `window_end`; stop = far side."""
    m15 = bars("15min")
    h4 = bars("4h")
    days = m15.index.normalize()
    for day in pd.unique(days):
        today = m15[days == day]
        hours = today.index.hour
        rng = today[(hours >= range_start) & (hours < range_end)]
        if len(rng) < (range_end - range_start) * 3:
            continue
        high, low = rng["h"].max(), rng["l"].min()
        a = today["atr"].iloc[len(rng) - 1] if len(rng) else np.nan
        if not np.isfinite(a) or high - low < 2 * a or high - low > 12 * a:
            continue
        window = today[(hours >= range_end) & (hours < window_end)]
        for k in range(len(window)):
            c = window["c"].iloc[k]
            d = 1 if c > high else -1 if c < low else 0
            if not d:
                continue
            ctx = h4.index.searchsorted(window.index[k]) - 1
            if ctx < 210 or not trend_ok(h4, ctx, d, trend):
                break
            stop = (high + low) / 2.0
            risk = abs(c - stop)
            end = window.index[k] + pd.Timedelta(hours=10)
            yield Plan(window.index[k] + pd.Timedelta(minutes=5), d, c, stop, c + d * target_r * risk, end)
            break


def ny_momentum(minutes: int, min_move_atr: float, target_r: float, trend: str) -> Iterable[Plan]:
    """Strong first move after the New York open (13:30 UTC in summer): trade its continuation."""
    m5 = gold().m5
    h4 = bars("4h")
    daily_atr = atr(gold().d1, 14)
    ny = m5.index.tz_convert(NY)
    for day, today in m5.groupby(ny.date):
        local = today.index.tz_convert(NY)
        start = pd.Timestamp(f"{day} 08:30", tz=NY)
        first = today[(local >= start) & (local < start + pd.Timedelta(minutes=minutes))]
        if len(first) < minutes // 5:
            continue
        ref = daily_atr.loc[:day].iloc[-2] if len(daily_atr.loc[:day]) > 1 else np.nan
        move = first["c"].iloc[-1] - first["o"].iloc[0]
        if not np.isfinite(ref) or abs(move) < min_move_atr * ref:
            continue
        d = 1 if move > 0 else -1
        ctx = h4.index.searchsorted(first.index[-1]) - 1
        if ctx < 210 or not trend_ok(h4, ctx, d, trend):
            continue
        entry = first["c"].iloc[-1]
        stop = first["l"].min() if d > 0 else first["h"].max()
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        end = pd.Timestamp(f"{day} 16:00", tz=NY).tz_convert("UTC")
        yield Plan(first.index[-1] + pd.Timedelta(minutes=5), d, entry, stop, entry + d * target_r * risk, end)


FAMILIES: dict[str, Callable[[], Iterable[tuple[str, Iterable[Plan]]]]] = {
    "donchian": lambda: (
        (f"donchian {rule} n{n} stop{s} t{t}R hold{h} {tr}", donchian_break(rule, n, s, t, h, tr))
        for rule, n, s, t, h, tr in itertools.product(
            ("1h", "4h"), (20, 30, 40), (1.5, 2.0), (0.75, 1.0, 1.5), (18, 30), ("none", "ema200", "ema50>200"))
    ),
    "rsi_pullback": lambda: (
        (f"rsi-pullback {rule} rsi{n}<{lv} stop{s} t{t}R hold{h} {tr}", trend_pullback_rsi(rule, n, lv, s, t, h, tr))
        for rule, n, lv, s, t, h, tr in itertools.product(
            ("1h", "4h"), (2, 3), (10, 20), (1.5, 2.0), (0.5, 0.75, 1.0), (12, 24), ("ema200", "ema50>200"))
    ),
    "ema_pullback": lambda: (
        (f"ema-pullback {rule} stop{s} t{t}R hold{h}", ema_pullback(rule, s, t, h))
        for rule, s, t, h in itertools.product(("1h", "4h"), (1.0, 1.5, 2.0), (0.75, 1.0, 1.5), (12, 24))
    ),
    "squeeze": lambda: (
        (f"squeeze {rule} look{lk} stop{s} t{t}R hold{h} {tr}", squeeze_break(rule, lk, s, t, h, tr))
        for rule, lk, s, t, h, tr in itertools.product(
            ("1h", "4h"), (60, 120), (1.5, 2.0), (1.0, 1.5), (18, 30), ("none", "ema200"))
    ),
    "session": lambda: (
        (f"session {a:02d}-{b:02d}->{w:02d} t{t}R {tr}", session_breakout(a, b, w, t, tr))
        for (a, b, w), t, tr in itertools.product(
            ((0, 6, 10), (0, 7, 11), (1, 7, 12), (12, 13, 16)), (1.0, 1.5, 2.0), ("none", "ema200"))
    ),
    "ny_momentum": lambda: (
        (f"ny-momentum {mins}m move>{mv}ATR t{t}R {tr}", ny_momentum(mins, mv, t, tr))
        for mins, mv, t, tr in itertools.product((30, 60), (0.2, 0.3), (1.0, 1.5), ("none", "ema200"))
    ),
}


def main(selected: list[str]) -> None:
    for family in selected or list(FAMILIES):
        tried = passed = 0
        print(f"\n# {family}", flush=True)
        for label, plans in FAMILIES[family]():
            res = run_all(gold(), weekend_safe(plans, False))
            ok, line = report(label, res)
            tried += 1
            if ok:
                passed += 1
                r = np.array([x["r"] for x in res])
                print(f"{line} | {len(r) / 3:.0f}/yr", flush=True)
        print(f"## {family}: {passed} of {tried} variants passed", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
