"""Classic, well-documented strategies tested on raw 3-year price history.

Costs: spread + slippage on both fills (per instrument). If a 5-minute bar
touches both the stop and the target, the stop is assumed hit first.
Results are in R (1R = the trade's planned risk), split into the first
24 months (to choose) and the last 12 months (to prove).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config.instruments import INSTRUMENTS  # noqa: E402
from scripts.history.sources import iter_months, load_month  # noqa: E402

MARKETS = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "US100", "US500"]
START, END = (2023, 10), (2026, 9)
SPLIT = pd.Timestamp("2025-10-01", tz="UTC")
NY = "America/New_York"
LONDON = "Europe/London"


def load_m5(market: str) -> pd.DataFrame:
    rows = []
    for year, month in iter_months(START, END):
        rows += load_month(market, "M5", year, month)
    frame = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).drop_duplicates("ts").sort_values("ts")
    frame.index = pd.to_datetime(frame["ts"], unit="s", utc=True)
    return frame[["o", "h", "l", "c"]]


def trading_day(index: pd.DatetimeIndex) -> pd.Index:
    """FX convention: the day rolls at 17:00 New York."""
    return (index.tz_convert(NY) + pd.Timedelta(hours=7)).date


def daily(m5: pd.DataFrame) -> pd.DataFrame:
    grouped = m5.groupby(trading_day(m5.index))
    frame = pd.DataFrame({
        "o": grouped["o"].first(), "h": grouped["h"].max(), "l": grouped["l"].min(), "c": grouped["c"].last(),
        "last_ts": grouped.apply(lambda g: g.index[-1]),
    })
    frame = frame[grouped.size() >= 24]  # skip stub days (holidays, Sunday opens)
    return frame


def resample(m5: pd.DataFrame, rule: str) -> pd.DataFrame:
    frame = m5.resample(rule, label="right", closed="right").agg({"o": "first", "h": "max", "l": "min", "c": "last"})
    return frame.dropna()


def atr(frame: pd.DataFrame, period: int) -> pd.Series:
    prev = frame["c"].shift(1)
    tr = pd.concat([frame["h"] - frame["l"], (frame["h"] - prev).abs(), (frame["l"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


@dataclass
class Plan:
    start: pd.Timestamp          # first bar whose prices can fill/close the trade
    direction: int               # +1 long, -1 short
    entry: Optional[float]       # None = market at the open of the start bar
    stop: float
    target: Optional[float]
    deadline: pd.Timestamp       # close at this bar's close if still open
    stop_entry: bool = False     # entry is a stop order at `entry` (fills only if touched)
    expire: Optional[pd.Timestamp] = None  # stop order cancelled if not filled by then


class Market:
    def __init__(self, name: str):
        self.name = name
        self.m5 = load_m5(name)
        self.ts = self.m5.index
        self.o, self.h = self.m5["o"].to_numpy(), self.m5["h"].to_numpy()
        self.l, self.c = self.m5["l"].to_numpy(), self.m5["c"].to_numpy()
        instrument = INSTRUMENTS[name]
        self.cost = float(instrument.typical_spread) + 2.0 * float(instrument.slippage)
        self.d1 = daily(self.m5)

    def pos(self, moment: pd.Timestamp) -> int:
        return int(self.ts.searchsorted(moment))

    def run(self, plan: Plan) -> Optional[dict]:
        i = self.pos(plan.start)
        end = min(self.pos(plan.deadline), len(self.ts) - 1)
        if i >= len(self.ts) or i > end:
            return None
        d = plan.direction
        if plan.stop_entry:
            expire = min(self.pos(plan.expire or plan.deadline), end)
            while i <= expire:
                if (d > 0 and self.h[i] >= plan.entry) or (d < 0 and self.l[i] <= plan.entry):
                    break
                i += 1
            else:
                return None
            entry = max(plan.entry, self.o[i]) if d > 0 else min(plan.entry, self.o[i])
        else:
            entry = self.o[i] if plan.entry is None else plan.entry
        risk = abs(entry - plan.stop)
        if risk <= 0 or (d > 0 and plan.stop >= entry) or (d < 0 and plan.stop <= entry):
            return None
        exit_price, how = None, "time"
        exit_at = end
        for j in range(i, end + 1):
            hit_stop = self.l[j] <= plan.stop if d > 0 else self.h[j] >= plan.stop
            hit_target = plan.target is not None and (self.h[j] >= plan.target if d > 0 else self.l[j] <= plan.target)
            if hit_stop:
                gap = self.o[j] < plan.stop if d > 0 else self.o[j] > plan.stop
                exit_price, how, exit_at = (self.o[j] if gap else plan.stop), "stop", j
                break
            if hit_target:
                exit_price, how, exit_at = plan.target, "target", j
                break
        if exit_price is None:
            exit_price = self.c[end]
        r = (d * (exit_price - entry) - self.cost) / risk
        return {"time": self.ts[i], "exit": self.ts[exit_at], "direction": d, "r": float(r), "how": how}


# --- strategies: each yields Plans for one market ------------------------------------------

def orb_ny(m: Market, minutes: int, target_r: Optional[float]) -> Iterable[Plan]:
    """Opening-range breakout at the New York cash open (Crabel; Zarattini & Aziz 2023)."""
    ny = m.m5.index.tz_convert(NY)
    for day, bars in m.m5.groupby(ny.date):
        local = bars.index.tz_convert(NY)
        open_time = pd.Timestamp(f"{day} 09:30", tz=NY)
        window = bars[(local > open_time) & (local <= open_time + pd.Timedelta(minutes=minutes))]
        if len(window) < minutes // 5:
            continue
        high, low = window["h"].max(), window["l"].min()
        start = (open_time + pd.Timedelta(minutes=minutes)).tz_convert("UTC")
        cutoff = pd.Timestamp(f"{day} 12:00", tz=NY).tz_convert("UTC")
        close = pd.Timestamp(f"{day} 15:55", tz=NY).tz_convert("UTC")
        size = high - low
        for d, level, stop in ((1, high, low), (-1, low, high)):
            target = None if target_r is None else level + d * target_r * size
            yield Plan(start, d, level, stop, target, close, stop_entry=True, expire=cutoff)


def london_breakout(m: Market, target_r: Optional[float]) -> Iterable[Plan]:
    """Break of the Asian range at the London open."""
    ldn = m.m5.index.tz_convert(LONDON)
    daily_atr = atr(m.d1, 14)
    for day, bars in m.m5.groupby(ldn.date):
        local = bars.index.tz_convert(LONDON)
        asia = bars[local.hour < 7]
        if len(asia) < 60:
            continue
        high, low = asia["h"].max(), asia["l"].min()
        ref = daily_atr.loc[:day].iloc[-2] if len(daily_atr.loc[:day]) > 1 else np.nan
        if not np.isfinite(ref) or (high - low) > 0.8 * ref or (high - low) < 0.15 * ref:
            continue  # only tidy ranges: not already run, not dead
        start = pd.Timestamp(f"{day} 07:00", tz=LONDON).tz_convert("UTC")
        cutoff = pd.Timestamp(f"{day} 11:00", tz=LONDON).tz_convert("UTC")
        close = pd.Timestamp(f"{day} 16:00", tz=LONDON).tz_convert("UTC")
        size = high - low
        for d, level, stop in ((1, high, low), (-1, low, high)):
            target = None if target_r is None else level + d * target_r * size
            yield Plan(start, d, level, stop, target, close, stop_entry=True, expire=cutoff)


def donchian(m: Market, rule: str, entry_n: int, exit_n: int) -> Iterable[Plan]:
    """Turtle-style channel breakout with a 2-ATR stop and a trailing channel exit."""
    bars = resample(m.m5, rule)
    a = atr(bars, 20)
    upper = bars["h"].rolling(entry_n).max().shift(1)
    lower = bars["l"].rolling(entry_n).min().shift(1)
    exit_low = bars["l"].rolling(exit_n).min().shift(1)
    exit_high = bars["h"].rolling(exit_n).max().shift(1)
    times, close = bars.index, bars["c"].to_numpy()
    k = entry_n + 25
    while k < len(bars) - 1:
        d = 1 if close[k] > upper.iloc[k] else -1 if close[k] < lower.iloc[k] else 0
        if d == 0 or not np.isfinite(a.iloc[k]):
            k += 1
            continue
        stop = close[k] - d * 2.0 * a.iloc[k]
        j = k + 1
        while j < len(bars) - 1 and not (d > 0 and close[j] < exit_low.iloc[j]) and not (d < 0 and close[j] > exit_high.iloc[j]):
            j += 1
        yield Plan(times[k] + pd.Timedelta(minutes=5), d, close[k], stop, None, times[j])
        k = j + 1


def rsi2(m: Market, low: float, high: float, shorts: bool) -> Iterable[Plan]:
    """Connors RSI(2): buy short dips in long uptrends, exit on a close above the 5-day average."""
    d1 = m.d1
    close = d1["c"]
    r2, sma200, sma5, a = rsi(close, 2), close.rolling(200).mean(), close.rolling(5).mean(), atr(d1, 10)
    k = 200
    while k < len(d1) - 1:
        d = 1 if close.iloc[k] > sma200.iloc[k] and r2.iloc[k] < low else 0
        if shorts and close.iloc[k] < sma200.iloc[k] and r2.iloc[k] > high:
            d = -1
        if d == 0:
            k += 1
            continue
        j = k + 1
        while j < min(k + 10, len(d1) - 1) and not (d * (close.iloc[j] - sma5.iloc[j]) > 0):
            j += 1
        start = d1["last_ts"].iloc[k] + pd.Timedelta(minutes=5)
        yield Plan(start, d, close.iloc[k], close.iloc[k] - d * 3.0 * a.iloc[k], None, d1["last_ts"].iloc[j])
        k = j + 1


def nr7(m: Market, target_r: Optional[float]) -> Iterable[Plan]:
    """Narrowest range of 7 days, then trade the next day's breakout (Crabel)."""
    d1 = m.d1
    rng = d1["h"] - d1["l"]
    is_nr7 = rng == rng.rolling(7).min()
    for k in range(7, len(d1) - 1):
        if not is_nr7.iloc[k]:
            continue
        high, low = d1["h"].iloc[k], d1["l"].iloc[k]
        start = d1["last_ts"].iloc[k] + pd.Timedelta(minutes=5)
        close = d1["last_ts"].iloc[k + 1]
        size = high - low
        for d, level, stop in ((1, high, low), (-1, low, high)):
            target = None if target_r is None else level + d * target_r * size
            yield Plan(start, d, level, stop, target, close, stop_entry=True, expire=close)


def turn_of_month(m: Market) -> Iterable[Plan]:
    """Long from the last trading day's close to the third trading day's close."""
    d1 = m.d1
    months = pd.Series([pd.Timestamp(day).strftime("%Y-%m") for day in d1.index], index=d1.index)
    a = atr(d1, 10)
    for k in range(20, len(d1) - 4):
        if months.iloc[k] != months.iloc[k + 1]:
            start = d1["last_ts"].iloc[k] + pd.Timedelta(minutes=5)
            yield Plan(start, 1, d1["c"].iloc[k], d1["c"].iloc[k] - 2.0 * a.iloc[k], None, d1["last_ts"].iloc[k + 3])


def bollinger_quiet(m: Market, sigma: float) -> Iterable[Plan]:
    """Fade stretches outside the Bollinger band in the quiet Asian session, target the middle."""
    h1 = resample(m.m5, "1h")
    mid = h1["c"].rolling(20).mean()
    sd = h1["c"].rolling(20).std()
    a = atr(h1, 14)
    hours = h1.index.hour
    for k in range(30, len(h1) - 13):
        if not (hours[k] >= 22 or hours[k] < 5):
            continue
        c = h1["c"].iloc[k]
        d = 1 if c < mid.iloc[k] - sigma * sd.iloc[k] else -1 if c > mid.iloc[k] + sigma * sd.iloc[k] else 0
        if d == 0:
            continue
        yield Plan(h1.index[k] + pd.Timedelta(minutes=5), d, c, c - d * 1.5 * a.iloc[k], mid.iloc[k], h1.index[k + 12])


STRATEGIES: dict[str, Callable[[Market], Iterable[Plan]]] = {
    "ORB-NY 15m, 2R": lambda m: orb_ny(m, 15, 2.0),
    "ORB-NY 15m, hold to close": lambda m: orb_ny(m, 15, None),
    "ORB-NY 30m, 1R": lambda m: orb_ny(m, 30, 1.0),
    "London breakout 1R": lambda m: london_breakout(m, 1.0),
    "London breakout 2R": lambda m: london_breakout(m, 2.0),
    "Donchian H4 20/10": lambda m: donchian(m, "4h", 20, 10),
    "Donchian H4 55/20": lambda m: donchian(m, "4h", 55, 20),
    "Donchian D1 20/10": lambda m: donchian(m, "1D", 20, 10),
    "RSI2 longs <10": lambda m: rsi2(m, 10, 90, False),
    "RSI2 both <10/>90": lambda m: rsi2(m, 10, 90, True),
    "RSI2 longs <5": lambda m: rsi2(m, 5, 95, False),
    "NR7 breakout 1R": lambda m: nr7(m, 1.0),
    "NR7 breakout to close": lambda m: nr7(m, None),
    "Turn of month": turn_of_month,
    "Bollinger quiet 2.0": lambda m: bollinger_quiet(m, 2.0),
    "Bollinger quiet 2.5": lambda m: bollinger_quiet(m, 2.5),
}


def summary(values: list[float]) -> str:
    if not values:
        return "  0       -       -        -"
    arr = np.array(values)
    wins = (arr > 0).mean() * 100
    return f"{len(arr):3d} {wins:5.0f}% {arr.mean():+7.3f} {arr.sum():+8.1f}"


def main(selected: Optional[list[str]] = None) -> None:
    print("strategy | market | FIRST 24m: n win% avg total | LAST 12m: n win% avg total")
    for name in selected or MARKETS:
        market = Market(name)
        for label, build in STRATEGIES.items():
            results = [res for res in (market.run(p) for p in build(market)) if res]
            first = [x["r"] for x in results if x["time"] < SPLIT]
            last = [x["r"] for x in results if x["time"] >= SPLIT]
            flag = "  <== both positive" if first and last and np.mean(first) > 0.05 and np.mean(last) > 0.05 and len(first) >= 20 and len(last) >= 8 else ""
            print(f"{label} | {name} | {summary(first)} | {summary(last)}{flag}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
