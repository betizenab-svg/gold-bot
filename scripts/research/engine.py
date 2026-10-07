"""Fast research engine: cached 5-minute history, resampled charts, indicators,
and trade resolution on 5-minute prices (stop first when a bar touches both),
with costs and the bot's weekend rule (no entry in the last 2 hours before
Friday 20:30 UTC; every trade closed by then).

Periods: discovery = the first 24 months (choose), hold-out = the last 12
months (prove, never used to choose).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config.instruments import INSTRUMENTS  # noqa: E402
from scripts.history.sources import iter_months, load_month  # noqa: E402

START, END = (2023, 10), (2026, 9)
CACHE = ROOT / "data" / "research" / "cache"
YEAR1 = int(pd.Timestamp("2024-10-01", tz="UTC").timestamp())
YEAR2 = int(pd.Timestamp("2025-10-01", tz="UTC").timestamp())  # hold-out starts here
WEEK = 7 * 86400
CUTOFF = 4 * 86400 + 20 * 3600 + 1800  # Friday 20:30 UTC, seconds after Monday 00:00
NO_ENTRY = 2 * 3600
RULES = {"H1": "1h", "H4": "4h", "D1": "D1"}
BAR_SECONDS = {"H1": 3600, "H4": 4 * 3600, "D1": 86400}


@dataclass
class Data:
    name: str
    ts: np.ndarray  # M5 bar open times, seconds
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    cost: float  # price units per trade: spread plus slippage on both fills


def load(name: str) -> Data:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{name}_M5.npz"
    if path.exists():
        z = np.load(path)
        ts, o, h, low, c = z["ts"], z["o"], z["h"], z["l"], z["c"]
    else:
        rows = []
        for year, month in iter_months(START, END):
            rows += load_month(name, "M5", year, month)
        arr = np.array(sorted({r[0]: r[:5] for r in rows}.values()), dtype=float)
        ts, o, h, low, c = arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
        np.savez(path, ts=ts, o=o, h=h, l=low, c=c)
    instrument = INSTRUMENTS[name]
    return Data(name, ts, o, h, low, c, float(instrument.typical_spread) + 2.0 * float(instrument.slippage))


def bars(data: Data, timeframe: str) -> pd.DataFrame:
    """OHLC bars labelled by their close time (seconds). Daily bars roll at 17:00 New York."""
    frame = pd.DataFrame(
        {"o": data.o, "h": data.h, "l": data.l, "c": data.c},
        index=pd.to_datetime(data.ts, unit="s", utc=True),
    )
    if timeframe == "D1":
        local = frame.index.tz_convert("America/New_York") + pd.Timedelta(hours=7)
        day = local.normalize()
        grouped = frame.groupby(day)
        out = pd.DataFrame({"o": grouped["o"].first(), "h": grouped["h"].max(),
                            "l": grouped["l"].min(), "c": grouped["c"].last()})
        out = out[grouped.size() >= 48]
        close = grouped.apply(lambda g: g.index[-1]).loc[out.index] + pd.Timedelta(minutes=5)
        out.index = pd.DatetimeIndex(close)
    else:
        out = frame.resample(RULES[timeframe], label="right", closed="right").agg(
            {"o": "first", "h": "max", "l": "min", "c": "last"}
        ).dropna()
        # The bar labelled T holds the 5-minute bar opening at T, so it closes at T + 5 min.
        out.index = out.index + pd.Timedelta(minutes=5)
    out["t"] = (out.index.as_unit("ns").asi8 // 1_000_000_000).astype(np.int64)
    return out


# --- indicators --------------------------------------------------------------------------------

def atr(f: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = f["c"].shift(1)
    tr = pd.concat([f["h"] - f["l"], (f["h"] - prev).abs(), (f["l"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


def adx(f: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up, down = f["h"].diff(), -f["l"].diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=f.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=f.index)
    prev = f["c"].shift(1)
    tr = pd.concat([f["h"] - f["l"], (f["h"] - prev).abs(), (f["l"] - prev).abs()], axis=1).max(axis=1)
    smooth = lambda s: s.ewm(alpha=1 / n, adjust=False).mean()  # noqa: E731
    atr_w = smooth(tr)
    di_plus, di_minus = 100 * smooth(plus) / atr_w, 100 * smooth(minus) / atr_w
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return smooth(dx), di_plus, di_minus


# --- trade resolution --------------------------------------------------------------------------

def cutoff_after(t: int) -> int:
    monday = t - ((t // 86400 + 3) % 7) * 86400 - t % 86400
    cut = monday + CUTOFF
    return cut if cut > t else cut + WEEK


def simulate(data: Data, f: pd.DataFrame, signal: np.ndarray, stop: np.ndarray, target_r: float,
             hold: int) -> np.ndarray:
    """Enter at the close of each signal bar (one trade at a time), stop `stop[k]`
    away, one target `target_r` x the stop away, closed after `hold` bars or before
    the weekend. Returns rows (entry time, exit time, direction, R)."""
    times, closes = f["t"].to_numpy(), f["c"].to_numpy()
    ts, high, low, op, close = data.ts, data.h, data.l, data.o, data.c
    out = []
    free_from = 0
    for k in np.flatnonzero(signal):
        t0 = int(times[k])
        if t0 < free_from:
            continue
        cut = cutoff_after(t0)
        if t0 >= cut - NO_ENTRY:
            continue
        risk = float(stop[k])
        if not np.isfinite(risk) or risk <= 0:
            continue
        i = int(np.searchsorted(ts, t0, side="left"))
        deadline = min(int(times[min(k + hold, len(times) - 1)]), cut)
        end = int(np.searchsorted(ts, deadline, side="left")) - 1
        if i >= len(ts) or end < i:
            continue
        d = int(signal[k])
        entry = float(closes[k])
        stop_price, goal = entry - d * risk, entry + d * target_r * risk
        if d > 0:
            hit_stop, hit_goal = low[i:end + 1] <= stop_price, high[i:end + 1] >= goal
        else:
            hit_stop, hit_goal = high[i:end + 1] >= stop_price, low[i:end + 1] <= goal
        js = int(np.argmax(hit_stop)) if hit_stop.any() else 1 << 30
        jg = int(np.argmax(hit_goal)) if hit_goal.any() else 1 << 30
        if js <= jg and js < (1 << 30):
            j = i + js
            gapped = op[j] < stop_price if d > 0 else op[j] > stop_price
            exit_price = op[j] if gapped else stop_price
        elif jg < (1 << 30):
            j = i + jg
            exit_price = goal
        else:
            j = end
            exit_price = close[end]
        r = (d * (exit_price - entry) - data.cost) / risk
        exit_time = int(ts[j]) + 300
        out.append((t0, exit_time, d, r))
        free_from = exit_time
    return np.array(out, dtype=float).reshape(-1, 4)


def simulate_levels(data: Data, f: pd.DataFrame, signal: np.ndarray, entry: np.ndarray, stop: np.ndarray,
                    target: np.ndarray, valid: int, hold: int, bar_seconds: int) -> np.ndarray:
    """Like simulate, with explicit price levels per signal bar. valid = 0 enters at
    the bar's close; valid > 0 places a limit order at `entry` for that many bars
    (filled at the limit, or at a better open). Returns (entry time, exit time, direction, R)."""
    times, closes = f["t"].to_numpy(), f["c"].to_numpy()
    ts, high, low, op, close = data.ts, data.h, data.l, data.o, data.c
    out = []
    free_from = 0
    for k in np.flatnonzero(signal):
        t0 = int(times[k])
        if t0 < free_from:
            continue
        d = int(signal[k])
        cut = cutoff_after(t0)
        stop_price, goal = float(stop[k]), float(target[k])
        if not (np.isfinite(stop_price) and np.isfinite(goal)):
            continue
        i = int(np.searchsorted(ts, t0, side="left"))
        if i >= len(ts):
            continue
        if valid <= 0:
            if t0 >= cut - NO_ENTRY:
                continue
            j, price = i, float(closes[k])
        else:
            last = int(np.searchsorted(ts, min(t0 + valid * bar_seconds, cut - NO_ENTRY), side="left")) - 1
            level = float(entry[k])
            if last < i or not np.isfinite(level):
                continue
            touched = low[i:last + 1] <= level if d > 0 else high[i:last + 1] >= level
            if not touched.any():
                continue
            j = i + int(np.argmax(touched))
            price = min(level, float(op[j])) if d > 0 else max(level, float(op[j]))
        risk = d * (price - stop_price)
        if risk <= 0 or d * (goal - price) <= 0:
            continue
        fill_time = int(ts[j])
        deadline = min(fill_time + hold * bar_seconds, cutoff_after(fill_time))
        end = int(np.searchsorted(ts, deadline, side="left")) - 1
        if end < j:
            continue
        if d > 0:
            hit_stop, hit_goal = low[j:end + 1] <= stop_price, high[j:end + 1] >= goal
        else:
            hit_stop, hit_goal = high[j:end + 1] >= stop_price, low[j:end + 1] <= goal
        js = int(np.argmax(hit_stop)) if hit_stop.any() else 1 << 30
        jg = int(np.argmax(hit_goal)) if hit_goal.any() else 1 << 30
        if js <= jg and js < (1 << 30):
            x = j + js
            gapped = op[x] < stop_price if d > 0 else op[x] > stop_price
            exit_price = op[x] if gapped and x > j else stop_price
        elif jg < (1 << 30):
            x, exit_price = j + jg, goal
        else:
            x, exit_price = end, close[end]
        r = (d * (exit_price - price) - data.cost) / risk
        exit_time = int(ts[x]) + 300
        out.append((fill_time, exit_time, d, r))
        free_from = exit_time
    return np.array(out, dtype=float).reshape(-1, 4)


# --- statistics --------------------------------------------------------------------------------

def stats(r: np.ndarray) -> dict:
    n = len(r)
    if n == 0:
        return {"n": 0, "avg": 0.0, "t": 0.0, "pf": 0.0, "win": 0.0, "total": 0.0}
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {
        "n": n,
        "avg": float(r.mean()),
        "t": float(r.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0,
        "pf": float(gains / losses) if losses > 0 else 99.0,
        "win": float((r > 0).mean()),
        "total": float(r.sum()),
    }


def periods(trades: np.ndarray) -> dict[str, dict]:
    t, r = trades[:, 0], trades[:, 3]
    return {
        "y1": stats(r[t < YEAR1]),
        "y2": stats(r[(t >= YEAR1) & (t < YEAR2)]),
        "disc": stats(r[t < YEAR2]),
        "hold": stats(r[t >= YEAR2]),
        "all": stats(r),
    }


@lru_cache(maxsize=None)
def market(name: str) -> Data:
    return load(name)
