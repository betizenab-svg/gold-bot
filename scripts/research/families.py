"""Strategy library: well-known families plus combinations of my own.

Every family turns a chart (bars labelled by close time) into a signal array:
+1 buy / -1 sell at that bar's close, 0 nothing. Only data up to the bar is used.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from scripts.research.engine import adx, atr, ema, rsi


class Ind:
    """Indicators for one chart, computed once on demand."""

    def __init__(self, f: pd.DataFrame):
        self.f = f
        self._cache: dict = {}

    def get(self, key: str, make: Callable[[], pd.Series]) -> pd.Series:
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def ema(self, n: int) -> pd.Series:
        return self.get(f"ema{n}", lambda: ema(self.f["c"], n))

    def atr(self, n: int = 14) -> pd.Series:
        return self.get(f"atr{n}", lambda: atr(self.f, n))

    def rsi(self, n: int) -> pd.Series:
        return self.get(f"rsi{n}", lambda: rsi(self.f["c"], n))

    def adx(self) -> tuple[pd.Series, pd.Series, pd.Series]:
        if "adx" not in self._cache:
            self._cache["adx"] = adx(self.f, 14)
        return self._cache["adx"]

    def band(self, n: int = 20) -> tuple[pd.Series, pd.Series]:
        mid = self.get(f"sma{n}", lambda: self.f["c"].rolling(n).mean())
        sd = self.get(f"sd{n}", lambda: self.f["c"].rolling(n).std())
        return mid, sd


def _sig(long: pd.Series, short: pd.Series) -> np.ndarray:
    return np.where(long.fillna(False).to_numpy(bool), 1, np.where(short.fillna(False).to_numpy(bool), -1, 0))


def _first(cond: pd.Series) -> pd.Series:
    """Only the first bar of a run of true bars."""
    cond = cond.fillna(False).astype(bool)
    return cond & ~cond.shift(1, fill_value=False)


def trend_up(x: Ind) -> pd.Series:
    return (x.f["c"] > x.ema(200)) & (x.ema(50) > x.ema(200))


def trend_down(x: Ind) -> pd.Series:
    return (x.f["c"] < x.ema(200)) & (x.ema(50) < x.ema(200))


# --- trend following ---------------------------------------------------------------------------

def donchian(x: Ind, n: int, filtered: bool) -> np.ndarray:
    c = x.f["c"]
    up, down = c > x.f["h"].shift(1).rolling(n).max(), c < x.f["l"].shift(1).rolling(n).min()
    if filtered:
        up, down = up & trend_up(x), down & trend_down(x)
    return _sig(up, down)


def squeeze(x: Ind, look: int, filtered: bool) -> np.ndarray:
    mid, sd = x.band(20)
    width = 4 * sd / mid
    tight = width <= width.rolling(look).min() * 1.05
    squeezed = tight | tight.shift(1, fill_value=False)
    c = x.f["c"]
    up, down = squeezed & (c > mid + 2 * sd), squeezed & (c < mid - 2 * sd)
    if filtered:
        up, down = up & trend_up(x), down & trend_down(x)
    return _sig(up, down)


def ema_pullback(x: Ind, fast: int, mid: int, slow: int) -> np.ndarray:
    f = x.f
    e1, e2, e3 = x.ema(fast), x.ema(mid), x.ema(slow)
    up = (e1 > e2) & (e2 > e3) & (f["l"] <= e1) & (f["c"] > e1) & (f["c"] > f["o"])
    down = (e1 < e2) & (e2 < e3) & (f["h"] >= e1) & (f["c"] < e1) & (f["c"] < f["o"])
    return _sig(up, down)


def ma_cross(x: Ind, fast: int, slow: int) -> np.ndarray:
    diff = x.ema(fast) - x.ema(slow)
    return _sig((diff > 0) & (diff.shift(1) <= 0), (diff < 0) & (diff.shift(1) >= 0))


def keltner(x: Ind, k: float, filtered: bool) -> np.ndarray:
    c, mid, a = x.f["c"], x.ema(20), x.atr(14)
    up, down = _first(c > mid + k * a), _first(c < mid - k * a)
    if filtered:
        up, down = up & trend_up(x), down & trend_down(x)
    return _sig(up, down)


def tsmom(x: Ind, n: int) -> np.ndarray:
    side = np.sign(x.f["c"] - x.f["c"].shift(n))
    return _sig((side > 0) & (side.shift(1) <= 0), (side < 0) & (side.shift(1) >= 0))


def supertrend(x: Ind, mult: float) -> np.ndarray:
    f, a = x.f, x.atr(10).to_numpy()
    hl2 = ((f["h"] + f["l"]) / 2).to_numpy()
    close = f["c"].to_numpy()
    n = len(f)
    upper, lower, trend = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n)
    for k in range(1, n):
        if not np.isfinite(a[k]):
            continue
        bu, bl = hl2[k] + mult * a[k], hl2[k] - mult * a[k]
        upper[k] = bu if not np.isfinite(upper[k - 1]) or bu < upper[k - 1] or close[k - 1] > upper[k - 1] else upper[k - 1]
        lower[k] = bl if not np.isfinite(lower[k - 1]) or bl > lower[k - 1] or close[k - 1] < lower[k - 1] else lower[k - 1]
        prev = trend[k - 1] or 1
        trend[k] = 1 if close[k] > upper[k - 1] else -1 if close[k] < lower[k - 1] else prev
    t = pd.Series(trend, index=f.index)
    return _sig((t > 0) & (t.shift(1) < 0), (t < 0) & (t.shift(1) > 0))


def adx_break(x: Ind, level: float) -> np.ndarray:
    a, plus, minus = x.adx()
    rise = (a > level) & (a.shift(1) <= level)
    return _sig(rise & (plus > minus), rise & (minus > plus))


def macd_trend(x: Ind, fast: int, slow: int) -> np.ndarray:
    line = x.ema(fast) - x.ema(slow)
    signal = line.ewm(span=9, adjust=False).mean()
    cross_up, cross_down = (line > signal) & (line.shift(1) <= signal.shift(1)), (line < signal) & (line.shift(1) >= signal.shift(1))
    c = x.f["c"]
    return _sig(cross_up & (line < 0) & (c > x.ema(200)), cross_down & (line > 0) & (c < x.ema(200)))


# --- volatility and structure ------------------------------------------------------------------

def nr_break(x: Ind, n: int, filtered: bool) -> np.ndarray:
    f = x.f
    rng = f["h"] - f["l"]
    narrow = (rng == rng.rolling(n).min()).shift(1, fill_value=False)
    up, down = narrow & (f["c"] > f["h"].shift(1)), narrow & (f["c"] < f["l"].shift(1))
    if filtered:
        up, down = up & trend_up(x), down & trend_down(x)
    return _sig(up, down)


def inside_break(x: Ind, filtered: bool) -> np.ndarray:
    f = x.f
    inside = (f["h"] < f["h"].shift(1)) & (f["l"] > f["l"].shift(1))
    mother_h, mother_l = f["h"].shift(2), f["l"].shift(2)
    prev_inside = inside.shift(1, fill_value=False)
    up, down = prev_inside & (f["c"] > mother_h), prev_inside & (f["c"] < mother_l)
    if filtered:
        up, down = up & trend_up(x), down & trend_down(x)
    return _sig(up, down)


def contraction_breakout(x: Ind, ratio: float, m: int) -> np.ndarray:
    """Mine: in an established trend, volatility contracts (short ATR well below
    long ATR), then the price closes beyond the last m bars: trade with the trend."""
    f = x.f
    quiet = (x.atr(5) / x.atr(50)).shift(1) < ratio
    up = trend_up(x) & (x.ema(50) > x.ema(50).shift(5)) & quiet & (f["c"] > f["h"].shift(1).rolling(m).max())
    down = trend_down(x) & (x.ema(50) < x.ema(50).shift(5)) & quiet & (f["c"] < f["l"].shift(1).rolling(m).min())
    return _sig(up, down)


def breakout_retest(x: Ind, n: int) -> np.ndarray:
    """Mine: after a close beyond the n-bar high, the price comes back to the broken
    level within 10 bars and closes back above it: buy the successful retest."""
    f = x.f
    a = x.atr(14)
    level_up = f["h"].shift(1).rolling(n).max()
    level_down = f["l"].shift(1).rolling(n).min()
    broke_up = f["c"] > level_up
    broke_down = f["c"] < level_down
    last_up = level_up.where(broke_up).ffill(limit=10)
    last_down = level_down.where(broke_down).ffill(limit=10)
    up = ~broke_up & (f["l"] <= last_up + 0.25 * a) & (f["c"] > last_up) & (f["c"] > f["o"])
    down = ~broke_down & (f["h"] >= last_down - 0.25 * a) & (f["c"] < last_down) & (f["c"] < f["o"])
    return _sig(_first(up), _first(down))


# --- mean reversion ----------------------------------------------------------------------------

def rsi_reversion(x: Ind, n: int, low: float) -> np.ndarray:
    r, c, trend = x.rsi(n), x.f["c"], x.ema(200)
    return _sig((r < low) & (c > trend), (r > 100 - low) & (c < trend))


def band_fade(x: Ind, sigma: float, quiet_only: bool) -> np.ndarray:
    mid, sd = x.band(20)
    c = x.f["c"]
    was_low, was_high = (c.shift(1) < (mid - sigma * sd).shift(1)), (c.shift(1) > (mid + sigma * sd).shift(1))
    up, down = was_low & (c > mid - sigma * sd), was_high & (c < mid + sigma * sd)
    if quiet_only:
        a = x.adx()[0]
        up, down = up & (a < 20), down & (a < 20)
    return _sig(up, down)


def zscore(x: Ind, n: int, z: float) -> np.ndarray:
    c = x.f["c"]
    score = (c - c.rolling(n).mean()) / c.rolling(n).std()
    quiet = x.adx()[0] < 20
    return _sig(_first((score < -z) & quiet), _first((score > z) & quiet))


def turtle_soup(x: Ind, n: int) -> np.ndarray:
    """Failed breakout: a new n-bar low that closes back above the old low (and mirror)."""
    f = x.f
    old_low, old_high = f["l"].shift(1).rolling(n).min(), f["h"].shift(1).rolling(n).max()
    return _sig((f["l"] < old_low) & (f["c"] > old_low), (f["h"] > old_high) & (f["c"] < old_high))


def ibs(x: Ind, threshold: float) -> np.ndarray:
    f = x.f
    strength = (f["c"] - f["l"]) / (f["h"] - f["l"]).replace(0, np.nan)
    trend = x.ema(200)
    return _sig((strength < threshold) & (f["c"] > trend), (strength > 1 - threshold) & (f["c"] < trend))


def streak(x: Ind, k: int) -> np.ndarray:
    c = x.f["c"]
    down = pd.Series(True, index=c.index)
    up = pd.Series(True, index=c.index)
    for j in range(k):
        down &= c.shift(j) < c.shift(j + 1)
        up &= c.shift(j) > c.shift(j + 1)
    trend = x.ema(200)
    return _sig(down & (c > trend), up & (c < trend))


# --- sessions (1-hour charts only) -------------------------------------------------------------

def london_break(x: Ind) -> np.ndarray:
    f = x.f
    local = f.index.tz_convert("Europe/London") - pd.Timedelta(minutes=65)  # bar start
    day, hour = local.normalize(), local.hour
    asia = pd.Series(hour < 7, index=f.index)
    asia_high = f["h"].where(asia).groupby(day).transform("max")
    asia_low = f["l"].where(asia).groupby(day).transform("min")
    window = pd.Series((hour >= 7) & (hour < 11), index=f.index)
    up = window & (f["c"] > asia_high)
    down = window & (f["c"] < asia_low)
    return _sig(up & (up.groupby(day).cumsum() == 1), down & (down.groupby(day).cumsum() == 1))


# --- combinations of my own --------------------------------------------------------------------

def regime_switch(x: Ind, n: int) -> np.ndarray:
    """Mine: trend days trade breakouts, quiet days fade the bands (ADX decides)."""
    a = x.adx()[0]
    trend = donchian(x, n, False)
    fade = band_fade(x, 2.0, False)
    return np.where(a.to_numpy() >= 25, trend, np.where(a.to_numpy() < 20, fade, 0))


def pullback_reclaim(x: Ind, n: int) -> np.ndarray:
    """Mine: after a strong push (close beyond the n-bar extreme within the last 10
    bars), the price dips below the 10 EMA and then closes back above it."""
    f = x.f
    e = x.ema(10)
    pushed_up = (f["c"] > f["h"].shift(1).rolling(n).max()).astype(float).rolling(10).max().fillna(0).astype(bool)
    pushed_down = (f["c"] < f["l"].shift(1).rolling(n).min()).astype(float).rolling(10).max().fillna(0).astype(bool)
    reclaim_up = (f["c"] > e) & (f["c"].shift(1) < e.shift(1))
    reclaim_down = (f["c"] < e) & (f["c"].shift(1) > e.shift(1))
    return _sig(pushed_up & reclaim_up & trend_up(x), pushed_down & reclaim_down & trend_down(x))


# --- more classic indicators -------------------------------------------------------------------

def ichimoku(x: Ind) -> np.ndarray:
    """Tenkan crosses kijun on the right side of the cloud."""
    f = x.f
    mid = lambda n: (f["h"].rolling(n).max() + f["l"].rolling(n).min()) / 2  # noqa: E731
    tenkan, kijun = mid(9), mid(26)
    span_a, span_b = ((tenkan + kijun) / 2).shift(26), mid(52).shift(26)
    top, bottom = np.maximum(span_a, span_b), np.minimum(span_a, span_b)
    cross_up = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))
    cross_down = (tenkan < kijun) & (tenkan.shift(1) >= kijun.shift(1))
    return _sig(cross_up & (f["c"] > top), cross_down & (f["c"] < bottom))


def psar(x: Ind, step: float = 0.02, cap: float = 0.2) -> np.ndarray:
    f = x.f
    high, low = f["h"].to_numpy(), f["l"].to_numpy()
    n = len(f)
    trend, sar, ep, af = np.zeros(n), np.zeros(n), 0.0, step
    trend[0], sar[0], ep = 1, low[0], high[0]
    for k in range(1, n):
        sar[k] = sar[k - 1] + af * (ep - sar[k - 1])
        if trend[k - 1] > 0:
            sar[k] = min(sar[k], low[k - 1], low[k - 2] if k > 1 else low[k - 1])
            if low[k] < sar[k]:
                trend[k], sar[k], ep, af = -1, ep, low[k], step
            else:
                trend[k] = 1
                if high[k] > ep:
                    ep, af = high[k], min(cap, af + step)
        else:
            sar[k] = max(sar[k], high[k - 1], high[k - 2] if k > 1 else high[k - 1])
            if high[k] > sar[k]:
                trend[k], sar[k], ep, af = 1, ep, high[k], step
            else:
                trend[k] = -1
                if low[k] < ep:
                    ep, af = low[k], min(cap, af + step)
    t = pd.Series(trend, index=f.index)
    return _sig((t > 0) & (t.shift(1) < 0) & trend_up(x), (t < 0) & (t.shift(1) > 0) & trend_down(x))


def cci_break(x: Ind, n: int = 20) -> np.ndarray:
    f = x.f
    typical = (f["h"] + f["l"] + f["c"]) / 3
    mean = typical.rolling(n).mean()
    dev = (typical - mean).abs().rolling(n).mean()
    cci = (typical - mean) / (0.015 * dev)
    return _sig((cci > 100) & (cci.shift(1) <= 100) & trend_up(x), (cci < -100) & (cci.shift(1) >= -100) & trend_down(x))


def stoch_pullback(x: Ind) -> np.ndarray:
    f = x.f
    low14, high14 = f["l"].rolling(14).min(), f["h"].rolling(14).max()
    k = 100 * (f["c"] - low14) / (high14 - low14).replace(0, np.nan)
    d = k.rolling(3).mean()
    up = (k > d) & (k.shift(1) <= d.shift(1)) & (d < 25) & trend_up(x)
    down = (k < d) & (k.shift(1) >= d.shift(1)) & (d > 75) & trend_down(x)
    return _sig(up, down)


def heikin_turn(x: Ind) -> np.ndarray:
    f = x.f
    ha_close = (f["o"] + f["h"] + f["l"] + f["c"]) / 4
    closes = ha_close.to_numpy()
    values = closes.copy()
    values[0] = (f["o"].iloc[0] + f["c"].iloc[0]) / 2
    for k in range(1, len(values)):
        values[k] = (values[k - 1] + closes[k - 1]) / 2
    green = pd.Series(closes > values, index=f.index)
    return _sig(green & ~green.shift(1, fill_value=True) & trend_up(x), ~green & green.shift(1, fill_value=False) & trend_down(x))


def aroon(x: Ind, n: int = 25) -> np.ndarray:
    f = x.f
    up = f["h"].rolling(n + 1).apply(lambda w: 100 * np.argmax(w) / n, raw=True)
    down = f["l"].rolling(n + 1).apply(lambda w: 100 * np.argmin(w) / n, raw=True)
    return _sig((up > down) & (up.shift(1) <= down.shift(1)) & (up > 70), (down > up) & (down.shift(1) <= up.shift(1)) & (down > 70))


Family = tuple[str, Callable[[Ind], np.ndarray], tuple[str, ...], str]

# (name, signal maker, chart timeframes, style: "trend" or "revert")
FAMILIES: list[Family] = [
    *[(f"donchian{n}{'_trend' if fl else ''}", (lambda x, n=n, fl=fl: donchian(x, n, fl)), ("H1", "H4", "D1"), "trend")
      for n in (20, 55) for fl in (False, True)],
    *[(f"squeeze{look}{'_trend' if fl else ''}", (lambda x, look=look, fl=fl: squeeze(x, look, fl)), ("H1", "H4", "D1"), "trend")
      for look in (60, 120) for fl in (False, True)],
    ("ema_pullback_20_50_200", lambda x: ema_pullback(x, 20, 50, 200), ("H1", "H4", "D1"), "trend"),
    ("ema_pullback_10_30_100", lambda x: ema_pullback(x, 10, 30, 100), ("H1", "H4", "D1"), "trend"),
    ("ma_cross_20_50", lambda x: ma_cross(x, 20, 50), ("H1", "H4", "D1"), "trend"),
    ("ma_cross_50_200", lambda x: ma_cross(x, 50, 200), ("H1", "H4"), "trend"),
    *[(f"keltner{k}{'_trend' if fl else ''}", (lambda x, k=k, fl=fl: keltner(x, k, fl)), ("H1", "H4", "D1"), "trend")
      for k in (1.5, 2.5) for fl in (False, True)],
    ("tsmom20", lambda x: tsmom(x, 20), ("H4", "D1"), "trend"),
    ("tsmom60", lambda x: tsmom(x, 60), ("H4", "D1"), "trend"),
    ("supertrend2", lambda x: supertrend(x, 2.0), ("H1", "H4", "D1"), "trend"),
    ("supertrend3", lambda x: supertrend(x, 3.0), ("H1", "H4", "D1"), "trend"),
    ("adx20", lambda x: adx_break(x, 20), ("H1", "H4", "D1"), "trend"),
    ("adx25", lambda x: adx_break(x, 25), ("H1", "H4", "D1"), "trend"),
    ("macd_12_26", lambda x: macd_trend(x, 12, 26), ("H1", "H4", "D1"), "trend"),
    *[(f"nr{n}{'_trend' if fl else ''}", (lambda x, n=n, fl=fl: nr_break(x, n, fl)), ("H4", "D1"), "trend")
      for n in (4, 7) for fl in (False, True)],
    ("inside", lambda x: inside_break(x, False), ("H4", "D1"), "trend"),
    ("inside_trend", lambda x: inside_break(x, True), ("H4", "D1"), "trend"),
    ("contraction_0.7", lambda x: contraction_breakout(x, 0.7, 5), ("H1", "H4", "D1"), "trend"),
    ("contraction_0.85", lambda x: contraction_breakout(x, 0.85, 5), ("H1", "H4", "D1"), "trend"),
    ("retest20", lambda x: breakout_retest(x, 20), ("H1", "H4", "D1"), "trend"),
    ("pullback_reclaim20", lambda x: pullback_reclaim(x, 20), ("H1", "H4", "D1"), "trend"),
    ("rsi2_10", lambda x: rsi_reversion(x, 2, 10), ("H1", "H4", "D1"), "revert"),
    ("rsi2_5", lambda x: rsi_reversion(x, 2, 5), ("H1", "H4", "D1"), "revert"),
    ("rsi3_15", lambda x: rsi_reversion(x, 3, 15), ("H1", "H4", "D1"), "revert"),
    ("band_fade2", lambda x: band_fade(x, 2.0, False), ("H1", "H4", "D1"), "revert"),
    ("band_fade2_quiet", lambda x: band_fade(x, 2.0, True), ("H1", "H4", "D1"), "revert"),
    ("band_fade2.5", lambda x: band_fade(x, 2.5, False), ("H1", "H4", "D1"), "revert"),
    ("zscore20_2", lambda x: zscore(x, 20, 2.0), ("H1", "H4", "D1"), "revert"),
    ("zscore50_2.5", lambda x: zscore(x, 50, 2.5), ("H1", "H4"), "revert"),
    ("turtle_soup20", lambda x: turtle_soup(x, 20), ("H1", "H4", "D1"), "revert"),
    ("turtle_soup55", lambda x: turtle_soup(x, 55), ("H1", "H4", "D1"), "revert"),
    ("ibs0.1", lambda x: ibs(x, 0.1), ("D1", "H4"), "revert"),
    ("ibs0.2", lambda x: ibs(x, 0.2), ("D1", "H4"), "revert"),
    ("streak3", lambda x: streak(x, 3), ("D1", "H4"), "revert"),
    ("streak4", lambda x: streak(x, 4), ("D1", "H4"), "revert"),
    ("london_break", london_break, ("H1",), "trend"),
    ("regime20", lambda x: regime_switch(x, 20), ("H1", "H4", "D1"), "trend"),
    ("ichimoku", ichimoku, ("H1", "H4", "D1"), "trend"),
    ("psar_trend", psar, ("H1", "H4", "D1"), "trend"),
    ("cci_trend", cci_break, ("H1", "H4", "D1"), "trend"),
    ("stoch_pullback", stoch_pullback, ("H1", "H4", "D1"), "trend"),
    ("heikin_turn", heikin_turn, ("H1", "H4", "D1"), "trend"),
    ("aroon25", aroon, ("H4", "D1"), "trend"),
]
