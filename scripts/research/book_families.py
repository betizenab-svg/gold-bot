"""Rules taken from the books in books/ (2026-10 reading pass), coded for 1-hour,
4-hour and daily charts. Two kinds:

- MARKET: return a signal array (+1 buy / -1 sell at the bar's close); the
  discovery grid supplies the ATR stop and the R target.
- LEVELS: return (signal, entry, stop, structure_target); entry is a limit price
  (or NaN = at the close), the stop is the book's structural stop.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from scripts.research.families import Ind, _first, _sig, trend_down, trend_up


def swings(f: pd.DataFrame, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Fractal swing highs/lows (n bars each side), usable only n bars later."""
    high, low = f["h"].to_numpy(), f["l"].to_numpy()
    is_high = np.zeros(len(f), bool)
    is_low = np.zeros(len(f), bool)
    for i in range(n, len(f) - n):
        window_h, window_l = high[i - n:i + n + 1], low[i - n:i + n + 1]
        is_high[i] = high[i] == window_h.max()
        is_low[i] = low[i] == window_l.min()
    return is_high, is_low


def _context(x: Ind, side: int) -> pd.Series:
    """Brooks guideline 73: 7 of the last 10 closes on one side of the 20 EMA."""
    above = (x.f["c"] > x.ema(20)).astype(float).rolling(10).sum()
    return above >= 7 if side > 0 else above <= 3


# --- MARKET families ----------------------------------------------------------------------------

def first_ema_touch(x: Ind, n: int = 20) -> np.ndarray:
    """Brooks: the first touch of the 20 EMA after 20+ bars away from it, then a break of the touch bar."""
    f, e = x.f, x.ema(20)
    above = (f["l"] > e).astype(float)
    below = (f["h"] < e).astype(float)
    touch_up = (f["l"].shift(1) <= e.shift(1)) & (above.shift(2).rolling(n).sum() >= n)
    touch_down = (f["h"].shift(1) >= e.shift(1)) & (below.shift(2).rolling(n).sum() >= n)
    return _sig(touch_up & (f["c"] > f["h"].shift(1)), touch_down & (f["c"] < f["l"].shift(1)))


def giant_bar_fade(x: Ind) -> np.ndarray:
    """Brooks/Wang: a giant trend bar far from the average after a long move is a climax; fade it."""
    f, a, e = x.f, x.atr(14), x.ema(20)
    rng, body = f["h"] - f["l"], (f["c"] - f["o"]).abs()
    giant = (rng >= 2 * rng.shift(1).rolling(20).mean()) & (body >= 0.6 * rng)
    up = giant & (f["c"] >= f["l"] + 0.8 * rng) & (f["c"] > e + 2 * a) & _context(x, 1)
    down = giant & (f["c"] <= f["h"] - 0.8 * rng) & (f["c"] < e - 2 * a) & _context(x, -1)
    return _sig(down, up)


def morning_star(x: Ind, filtered: bool) -> np.ndarray:
    """Candlestick Bible: big candle, small candle at a 10-bar extreme, then a close past the first body's midpoint."""
    f = x.f
    body = (f["c"] - f["o"]).abs()
    avg_body = body.shift(1).rolling(10).mean()
    first_bear = (f["c"].shift(2) < f["o"].shift(2)) & (body.shift(2) >= avg_body.shift(2))
    first_bull = (f["c"].shift(2) > f["o"].shift(2)) & (body.shift(2) >= avg_body.shift(2))
    small = body.shift(1) <= 0.3 * body.shift(2)
    mid = (f["o"].shift(2) + f["c"].shift(2)) / 2
    low_star = f["l"].shift(1) == f["l"].rolling(10).min().shift(1)
    high_star = f["h"].shift(1) == f["h"].rolling(10).max().shift(1)
    up = first_bear & small & low_star & (f["c"] > f["o"]) & (f["c"] > mid)
    down = first_bull & small & high_star & (f["c"] < f["o"]) & (f["c"] < mid)
    if filtered:
        up, down = up & trend_up(x), down & trend_down(x)
    return _sig(up, down)


def failed_failure(x: Ind, n: int = 20) -> np.ndarray:
    """Brooks: a breakout fails (closes back below the breakout bar), then the failure fails: re-enter."""
    f = x.f
    high, low, close = f["h"].to_numpy(), f["l"].to_numpy(), f["c"].to_numpy()
    level_up = f["h"].shift(1).rolling(n).max().to_numpy()
    level_down = f["l"].shift(1).rolling(n).min().to_numpy()
    out = np.zeros(len(f), int)
    for k in range(n + 10, len(f)):
        for j in range(k - 8, k - 1):
            if close[j] > level_up[j]:
                fails = [m for m in range(j + 1, min(j + 3, k)) if close[m] < low[j]]
                if fails and close[k] > high[fails[0]:k].max():
                    out[k] = 1
                    break
            if close[j] < level_down[j]:
                fails = [m for m in range(j + 1, min(j + 3, k)) if close[m] > high[j]]
                if fails and close[k] < low[fails[0]:k].min():
                    out[k] = -1
                    break
    return out


def double_bottom(x: Ind) -> np.ndarray:
    """Forex chart patterns (TradingSpine): two equal swing lows, then two closes above the neckline."""
    f, a = x.f, x.atr(14).to_numpy()
    high, low, close = f["h"].to_numpy(), f["l"].to_numpy(), f["c"].to_numpy()
    is_high, is_low = swings(f, 3)
    out = np.zeros(len(f), int)
    lows, highs = [], []
    for k in range(len(f)):
        i = k - 3
        if i >= 0 and is_low[i]:
            lows.append(i)
        if i >= 0 and is_high[i]:
            highs.append(i)
        if not np.isfinite(a[k]) or k < 2:
            continue
        if len(lows) >= 2:
            l1, l2 = lows[-2], lows[-1]
            neck = high[l1:l2 + 1].max()
            if l2 - l1 >= 5 and abs(low[l2] - low[l1]) <= 0.5 * a[k] and close[k] > neck and close[k - 1] > neck \
                    and close[k - 2] <= neck and low[l2 + 1:k + 1].min() > min(low[l1], low[l2]):
                out[k] = 1
        if len(highs) >= 2:
            h1, h2 = highs[-2], highs[-1]
            neck = low[h1:h2 + 1].min()
            if h2 - h1 >= 5 and abs(high[h2] - high[h1]) <= 0.5 * a[k] and close[k] < neck and close[k - 1] < neck \
                    and close[k - 2] >= neck and high[h2 + 1:k + 1].max() < max(high[h1], high[h2]):
                out[k] = -1
    return out


def spike_flag(x: Ind) -> np.ndarray:
    """Spike and flag: a fast 3-ATR push, a shallow pause, then a close above the pause."""
    f, a = x.f, x.atr(14).to_numpy()
    high, low, close, op = f["h"].to_numpy(), f["l"].to_numpy(), f["c"].to_numpy(), f["o"].to_numpy()
    out = np.zeros(len(f), int)
    for k in range(40, len(f)):
        if not np.isfinite(a[k]):
            continue
        for p in range(k - 20, k - 3):  # p = end of the spike
            start = p - 8
            push_up = close[p] - low[start:p + 1].min()
            push_down = high[start:p + 1].max() - close[p]
            flag = slice(p + 1, k)
            if push_up >= 3 * a[p] and (close[start:p + 1] > op[start:p + 1]).mean() >= 0.7:
                top = high[p:k].max()
                if high[p] == top and low[flag].min() >= high[p] - 0.5 * push_up and close[k] > high[flag].max() \
                        and close[k - 1] <= high[p + 1:k - 1].max(initial=-np.inf):
                    out[k] = 1
                    break
            if push_down >= 3 * a[p] and (close[start:p + 1] < op[start:p + 1]).mean() >= 0.7:
                bottom = low[p:k].min()
                if low[p] == bottom and high[flag].max() <= low[p] + 0.5 * push_down and close[k] < low[flag].min() \
                        and close[k - 1] >= low[p + 1:k - 1].min(initial=np.inf):
                    out[k] = -1
                    break
    return out


def macd_adx(x: Ind) -> np.ndarray:
    """Swing Trading for Dummies: MACD crosses its signal line while ADX > 20."""
    line = x.ema(12) - x.ema(26)
    signal = line.ewm(span=9, adjust=False).mean()
    strong = x.adx()[0] > 20
    up = (line > signal) & (line.shift(1) <= signal.shift(1)) & strong
    down = (line < signal) & (line.shift(1) >= signal.shift(1)) & strong
    return _sig(up, down)


def range_stoch(x: Ind) -> np.ndarray:
    """Swing Trading for Dummies: in a range (ADX <= 20), stochastic %K crosses %D out of oversold/overbought."""
    f = x.f
    low14, high14 = f["l"].rolling(14).min(), f["h"].rolling(14).max()
    k = 100 * (f["c"] - low14) / (high14 - low14).replace(0, np.nan)
    d = k.rolling(3).mean()
    quiet = x.adx()[0] <= 20
    up = (k > d) & (k.shift(1) <= d.shift(1)) & (k.shift(1) < 20) & quiet
    down = (k < d) & (k.shift(1) >= d.shift(1)) & (k.shift(1) > 80) & quiet
    return _sig(up, down)


def stoch_trend(x: Ind) -> np.ndarray:
    """Boxer: in an uptrend buy slow-stochastic exits from oversold; mirror in downtrends."""
    f = x.f
    sma50 = f["c"].rolling(50).mean()
    rising, falling = (f["c"] > sma50) & (sma50 > sma50.shift(5)), (f["c"] < sma50) & (sma50 < sma50.shift(5))

    def full(n: int, smooth: int, signal: int) -> pd.Series:
        raw = 100 * (f["c"] - f["l"].rolling(n).min()) / (f["h"].rolling(n).max() - f["l"].rolling(n).min()).replace(0, np.nan)
        return raw.rolling(smooth).mean().rolling(signal).mean()

    slow_up, slow_down = full(20, 5, 5), full(10, 3, 3)
    return _sig(rising & (slow_up > 20) & (slow_up.shift(1) <= 20), falling & (slow_down < 80) & (slow_down.shift(1) >= 80))


def band_ride_cci(x: Ind) -> np.ndarray:
    """Boxer: after a close beyond a Bollinger band, buy CCI(10) recoveries from -100 (mirror)."""
    f = x.f
    mid, sd = x.band(20)
    close = f["c"].to_numpy()
    upper, lower, m = (mid + 2 * sd).to_numpy(), (mid - 2 * sd).to_numpy(), mid.to_numpy()
    regime = np.zeros(len(f))
    for k in range(1, len(f)):
        regime[k] = regime[k - 1]
        if close[k] > upper[k]:
            regime[k] = 1
        elif close[k] < lower[k]:
            regime[k] = -1
        elif (regime[k] > 0 and m[k] < m[k - 1] and close[k] < m[k]) or (regime[k] < 0 and m[k] > m[k - 1] and close[k] > m[k]):
            regime[k] = 0
    typical = (f["h"] + f["l"] + f["c"]) / 3
    mean = typical.rolling(10).mean()
    cci = (typical - mean) / (0.015 * (typical - mean).abs().rolling(10).mean())
    r = pd.Series(regime, index=f.index)
    return _sig((r > 0) & (cci > -100) & (cci.shift(1) <= -100), (r < 0) & (cci < 100) & (cci.shift(1) >= 100))


def ma_channel(x: Ind) -> np.ndarray:
    """Kennedy: SMA5 of closes crosses above the SMA20 of highs (buy) or below the SMA20 of lows (sell)."""
    f = x.f
    fast, top, bottom = f["c"].rolling(5).mean(), f["h"].rolling(20).mean(), f["l"].rolling(20).mean()
    return _sig((fast > top) & (fast.shift(1) <= top.shift(1)), (fast < bottom) & (fast.shift(1) >= bottom.shift(1)))


# --- LEVEL families: (signal, entry or NaN for market, stop, structure target or NaN) ------------

Levels = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]


def outside_bar(x: Ind) -> Levels:
    """Langer's daily 'engulfing' = outside bar with the trend; stop at the other end of the bar."""
    f, a = x.f, x.atr(14)
    sma50 = f["c"].rolling(50).mean()
    outside = (f["h"] > f["h"].shift(1)) & (f["l"] < f["l"].shift(1))
    up = outside & (f["c"] > f["o"]) & (f["c"] > sma50) & (sma50 > sma50.shift(5))
    down = outside & (f["c"] < f["o"]) & (f["c"] < sma50) & (sma50 < sma50.shift(5))
    sig = _sig(up, down)
    stop = np.where(sig > 0, (f["l"] - 0.1 * a).to_numpy(), (f["h"] + 0.1 * a).to_numpy())
    return sig, np.full(len(f), np.nan), stop, np.full(len(f), np.nan)


def day_of_strength(x: Ind) -> Levels:
    """Swing Trading for Dummies: ADX trend, three lower highs, then the break of the last lower high."""
    f, a = x.f, x.atr(14)
    adx_, plus, minus = x.adx()
    h, low = f["h"], f["l"]
    up = (adx_ > 20) & (plus > minus) & (h.shift(1) < h.shift(2)) & (h.shift(2) < h.shift(3)) & (f["c"] > h.shift(1))
    down = (adx_ > 20) & (minus > plus) & (low.shift(1) > low.shift(2)) & (low.shift(2) > low.shift(3)) & (f["c"] < low.shift(1))
    sig = _sig(up, down)
    stop = np.where(sig > 0, (low.rolling(4).min() - 0.1 * a).to_numpy(), (h.rolling(4).max() + 0.1 * a).to_numpy())
    return sig, np.full(len(f), np.nan), stop, np.full(len(f), np.nan)


def band_pierce(x: Ind) -> Levels:
    """Langer's scalp, slower: with the trend, a dip through the lower band, then the first bullish break."""
    f, a = x.f, x.atr(14)
    mid, sd = x.band(20)
    pierced_low = (f["l"] < mid - 2 * sd).astype(float).rolling(3).max().shift(1) > 0
    pierced_high = (f["h"] > mid + 2 * sd).astype(float).rolling(3).max().shift(1) > 0
    up = trend_up(x) & pierced_low & (f["c"] > f["o"]) & (f["c"] > f["h"].shift(1))
    down = trend_down(x) & pierced_high & (f["c"] < f["o"]) & (f["c"] < f["l"].shift(1))
    sig = _sig(_first(up), _first(down))
    stop = np.where(sig > 0, (f["l"].rolling(5).min() - 0.1 * a).to_numpy(), (f["h"].rolling(5).max() + 0.1 * a).to_numpy())
    return sig, np.full(len(f), np.nan), stop, np.full(len(f), np.nan)


def monday_panic(x: Ind) -> Levels:
    """Kratter: buy the close of a Monday that closes in the bottom 10% of its 3-day range, sell Wednesday."""
    f, a = x.f, x.atr(14)
    local = (f.index - pd.Timedelta(minutes=10)).tz_convert("America/New_York")
    monday = pd.Series(local.weekday == 0, index=f.index)
    pct = 100 * (f["c"] - f["l"].rolling(3).min()) / (f["h"].rolling(3).max() - f["l"].rolling(3).min()).replace(0, np.nan)
    sig = _sig(monday & (pct <= 10), pd.Series(False, index=f.index))
    stop = (f["c"] - 3 * a).to_numpy()
    return sig, np.full(len(f), np.nan), stop, (f["c"] + 50 * a).to_numpy()


def supply_demand(x: Ind, filtered: bool) -> Levels:
    """Set and Forget: a fresh base that price left with force and that broke structure; buy the first return."""
    f, a = x.f, x.atr(14).to_numpy()
    o, h, low, c = (f[k].to_numpy() for k in ("o", "h", "l", "c"))
    rng = h - low
    body = np.abs(c - o)
    is_high, is_low = swings(f, 3)
    up_ok = trend_up(x).to_numpy() if filtered else np.ones(len(f), bool)
    down_ok = trend_down(x).to_numpy() if filtered else np.ones(len(f), bool)
    sig, entry, stop, target = np.zeros(len(f), int), np.full(len(f), np.nan), np.full(len(f), np.nan), np.full(len(f), np.nan)
    last_high, last_low = np.nan, np.nan
    for k in range(5, len(f)):
        if is_high[k - 3]:
            last_high = h[k - 3]
        if is_low[k - 3]:
            last_low = low[k - 3]
        if not np.isfinite(a[k]) or rng[k] <= 0 or body[k] <= 0.5 * rng[k]:
            continue
        base = []
        for j in range(k - 1, k - 4, -1):
            if rng[j] > 0 and body[j] <= 0.5 * rng[j]:
                base.append(j)
            else:
                break
        if not base:
            continue
        top_body = max(max(o[j], c[j]) for j in base)
        bottom_body = min(min(o[j], c[j]) for j in base)
        base_high, base_low = max(h[j] for j in base), min(low[j] for j in base)
        if c[k] > o[k] and up_ok[k] and np.isfinite(last_high) and c[k] > last_high and c[k] - top_body >= 2 * (top_body - base_low):
            sig[k], entry[k], stop[k], target[k] = 1, top_body + 0.05 * a[k], base_low - 0.15 * a[k], np.nan
        elif c[k] < o[k] and down_ok[k] and np.isfinite(last_low) and c[k] < last_low and bottom_body - c[k] >= 2 * (base_high - bottom_body):
            sig[k], entry[k], stop[k], target[k] = -1, bottom_body - 0.05 * a[k], base_high + 0.15 * a[k], np.nan
    return sig, entry, stop, target


def ote(x: Ind) -> Levels:
    """Market Makers Method: after a new swing high, buy the 70.5% retracement of the leg; stop below the leg start; target the high."""
    f, a = x.f, x.atr(14).to_numpy()
    h, low = f["h"].to_numpy(), f["l"].to_numpy()
    is_high, is_low = swings(f, 3)
    sig, entry, stop, target = np.zeros(len(f), int), np.full(len(f), np.nan), np.full(len(f), np.nan), np.full(len(f), np.nan)
    last_low, last_high = -1, -1
    for k in range(25, len(f)):
        i = k - 3
        prior_low, prior_high = last_low, last_high
        if is_low[i]:
            last_low = i
        if is_high[i]:
            last_high = i
            if prior_low >= 0 and np.isfinite(a[k]) and h[i] >= h[max(0, i - 20):i + 1].max():
                start = prior_low
                leg = h[i] - low[start]
                if leg >= 1.5 * a[k]:
                    sig[k], entry[k], stop[k], target[k] = 1, h[i] - 0.705 * leg, low[start] - 0.25 * a[k], h[i]
        if is_low[i] and sig[k] == 0:
            if prior_high >= 0 and np.isfinite(a[k]) and low[i] <= low[max(0, i - 20):i + 1].min():
                start = prior_high
                leg = h[start] - low[i]
                if leg >= 1.5 * a[k]:
                    sig[k], entry[k], stop[k], target[k] = -1, low[i] + 0.705 * leg, h[start] + 0.25 * a[k], low[i]
    return sig, entry, stop, target


def symmetry(x: Ind) -> Levels:
    """Boroden: in a trend, the next pullback tends to equal the last one; buy there, target the 1.272 extension."""
    f, a = x.f, x.atr(14).to_numpy()
    h, low = f["h"].to_numpy(), f["l"].to_numpy()
    is_high, is_low = swings(f, 3)
    sig, entry, stop, target = np.zeros(len(f), int), np.full(len(f), np.nan), np.full(len(f), np.nan), np.full(len(f), np.nan)
    points: list[tuple[int, str]] = []
    for k in range(10, len(f)):
        i = k - 3
        new = None
        if is_high[i]:
            points.append((i, "H"))
            new = "H"
        elif is_low[i]:
            points.append((i, "L"))
            new = "L"
        if new is None or len(points) < 4 or not np.isfinite(a[k]):
            continue
        p4, p3, p2, p1 = points[-1], points[-2], points[-3], points[-4]
        kinds = "".join(t for _, t in (p1, p2, p3, p4))
        if kinds == "LHLH" and h[p4[0]] > h[p2[0]] and low[p3[0]] > low[p1[0]]:
            depth = h[p2[0]] - low[p3[0]]
            level = h[p4[0]] - depth
            sig[k], entry[k], stop[k], target[k] = 1, level, level - 0.5 * a[k], level + 1.272 * depth
        elif kinds == "HLHL" and low[p4[0]] < low[p2[0]] and h[p3[0]] < h[p1[0]]:
            depth = h[p3[0]] - low[p2[0]]
            level = low[p4[0]] + depth
            sig[k], entry[k], stop[k], target[k] = -1, level, level + 0.5 * a[k], level - 1.272 * depth
    return sig, entry, stop, target


MarketFamily = tuple[str, Callable[[Ind], np.ndarray], tuple[str, ...]]
LevelFamily = tuple[str, Callable[[Ind], Levels], tuple[str, ...]]

MARKET_FAMILIES: list[MarketFamily] = [
    ("book_first_ema_touch", first_ema_touch, ("H1", "H4", "D1")),
    ("book_giant_bar_fade", giant_bar_fade, ("H1", "H4", "D1")),
    ("book_morning_star", lambda x: morning_star(x, False), ("H4", "D1")),
    ("book_morning_star_trend", lambda x: morning_star(x, True), ("H4", "D1")),
    ("book_failed_failure", failed_failure, ("H1", "H4", "D1")),
    ("book_double_bottom", double_bottom, ("H1", "H4", "D1")),
    ("book_spike_flag", spike_flag, ("H1", "H4", "D1")),
    ("book_macd_adx", macd_adx, ("H1", "H4", "D1")),
    ("book_range_stoch", range_stoch, ("H1", "H4", "D1")),
    ("book_stoch_trend", stoch_trend, ("H1", "H4", "D1")),
    ("book_band_ride_cci", band_ride_cci, ("H1", "H4", "D1")),
    ("book_ma_channel", ma_channel, ("H1", "H4", "D1")),
]
LEVEL_FAMILIES: list[LevelFamily] = [
    ("book_outside_bar", outside_bar, ("H4", "D1")),
    ("book_day_of_strength", day_of_strength, ("H1", "H4", "D1")),
    ("book_band_pierce", band_pierce, ("H1", "H4")),
    ("book_monday_panic", monday_panic, ("D1",)),
    ("book_supply_demand", lambda x: supply_demand(x, False), ("H1", "H4", "D1")),
    ("book_supply_demand_trend", lambda x: supply_demand(x, True), ("H1", "H4", "D1")),
    ("book_ote", ote, ("H1", "H4", "D1")),
    ("book_symmetry", symmetry, ("H1", "H4", "D1")),
]
