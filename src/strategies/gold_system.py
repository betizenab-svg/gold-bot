"""Gold 4-hour system: three trend triggers that each made money after costs in
every one of the last 3 years of gold history, with the trade closed before
every weekend (research: scripts/research/gold_lab.py and gold_system.py).

- GOLD_BREAKOUT: the 4-hour close breaks the highest high / lowest low of the
  previous 30 candles (Donchian / Turtle channel breakout).
- GOLD_SQUEEZE: Bollinger bands at their narrowest of 60 candles, then a
  close outside the band (volatility squeeze breakout).
- GOLD_PULLBACK: EMA20 > EMA50 > EMA200 (or the reverse); the candle dips to
  the EMA20 and closes back in the trend direction (trend pullback).

Every trade has one fixed plan: enter at the candle close, stop a set number
of ATRs away, one target at 1R (no half-close, no break-even move), and a
time limit in candles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from config.instruments import get_instrument, history_key
from src.domain.candle import Candle

SYSTEM_SYMBOL = "XAUUSD_H4"
SYSTEM_TIMEFRAME = "H4"
BREAKOUT, SQUEEZE, PULLBACK = "GOLD_BREAKOUT", "GOLD_SQUEEZE", "GOLD_PULLBACK"
MAX_SAME_DIRECTION = 2


@dataclass(frozen=True)
class Plan:
    stop_atr: float
    target_r: float
    hold_candles: int
    quiet_candles: int  # no new signal from the same trigger for this many candles


PLANS: dict[str, Plan] = {
    BREAKOUT: Plan(1.5, 1.0, 30, 10),
    SQUEEZE: Plan(1.5, 1.0, 30, 6),
    PULLBACK: Plan(2.0, 1.0, 24, 3),
}
TRIGGER_TEXT = {
    BREAKOUT: "the 4-hour close broke the {side} of the last 30 candles",
    SQUEEZE: "gold broke out of its tightest range in 60 candles",
    PULLBACK: "a pullback to the 20-period average inside a strong {trend}trend",
}
MIN_CANDLES = {BREAKOUT: 32, SQUEEZE: 82, PULLBACK: 250}


def is_fixed_plan(strategy: Any) -> bool:
    return str(strategy or "").upper() in PLANS


def signal_markets() -> list[str]:
    """Markets that make signals, so the ones history tests must cover."""
    from config import settings
    from config.instruments import INSTRUMENTS

    if settings.GOLD_SYSTEM_ONLY:
        return [SYSTEM_SYMBOL]
    return [name for name, instrument in INSTRUMENTS.items() if instrument.history_source]


def hold_candles(strategy: Any) -> Optional[int]:
    plan = PLANS.get(str(strategy or "").upper())
    return plan.hold_candles if plan else None


def _atr(candles: Sequence[Candle], period: int = 14) -> float:
    """Mean true range of the last `period` candles (the current one included)."""
    if len(candles) < period + 1:
        return 0.0
    total = 0.0
    for previous, current in zip(candles[-period - 1:-1], candles[-period:]):
        high, low, prev_close = float(current.high), float(current.low), float(previous.close)
        total += max(high - low, abs(high - prev_close), abs(low - prev_close))
    return total / period


def _ema(values: Sequence[float], span: int) -> float:
    alpha = 2.0 / (span + 1.0)
    value = values[0]
    for x in values[1:]:
        value = alpha * x + (1.0 - alpha) * value
    return value


def _band(closes: Sequence[float]) -> tuple[float, float]:
    """Mean and sample standard deviation (Bollinger, 20 periods)."""
    n = len(closes)
    mean = sum(closes) / n
    var = sum((c - mean) ** 2 for c in closes) / (n - 1)
    return mean, math.sqrt(var)


def breakout(candles: Sequence[Candle], n: int = 30) -> int:
    if len(candles) < MIN_CANDLES[BREAKOUT]:
        return 0
    previous = candles[-n - 1:-1]
    close = float(candles[-1].close)
    if close > max(float(c.high) for c in previous):
        return 1
    if close < min(float(c.low) for c in previous):
        return -1
    return 0


def squeeze(candles: Sequence[Candle], look: int = 60) -> int:
    if len(candles) < MIN_CANDLES[SQUEEZE]:
        return 0
    closes = [float(c.close) for c in candles]
    widths = []
    for end in range(len(closes) - look - 1, len(closes) + 1):
        mean, sd = _band(closes[end - 20:end])
        widths.append(4.0 * sd / mean if mean else math.inf)
    # widths[-1] is the current candle, widths[-2] the previous one.
    squeezed = any(widths[i] <= min(widths[i - look + 1:i + 1]) * 1.05 for i in (len(widths) - 2, len(widths) - 1))
    if not squeezed:
        return 0
    mean, sd = _band(closes[-20:])
    close = closes[-1]
    if close > mean + 2.0 * sd:
        return 1
    if close < mean - 2.0 * sd:
        return -1
    return 0


def pullback(candles: Sequence[Candle]) -> int:
    if len(candles) < MIN_CANDLES[PULLBACK]:
        return 0
    closes = [float(c.close) for c in candles]
    e20, e50, e200 = _ema(closes, 20), _ema(closes, 50), _ema(closes, 200)
    current = candles[-1]
    o, h, low, c = float(current.open), float(current.high), float(current.low), float(current.close)
    if e20 > e50 > e200 and low <= e20 and c > e20 and c > o:
        return 1
    if e20 < e50 < e200 and h >= e20 and c < e20 and c < o:
        return -1
    return 0


TRIGGERS = ((BREAKOUT, breakout), (SQUEEZE, squeeze), (PULLBACK, pullback))


def build_setup(candles: Sequence[Candle], strategy: str, direction: int) -> Optional[dict[str, Any]]:
    current = candles[-1]
    plan = PLANS[strategy]
    atr = _atr(candles)
    if atr <= 0:
        return None
    close = float(current.close)
    risk = plan.stop_atr * atr
    nd = get_instrument(current.symbol).price_decimals
    word = "LONG" if direction > 0 else "SHORT"
    why = TRIGGER_TEXT[strategy].format(
        side="high" if direction > 0 else "low", trend="up" if direction > 0 else "down"
    )
    return {
        "symbol": current.symbol,
        "timeframe": current.timeframe,
        "strategy": strategy,
        "trade_direction": word,
        "order_type": "MARKET",
        "fixed_plan": True,
        "entry_price": round(close, nd),
        "sl_price": round(close - direction * risk, nd),
        "tp_price": round(close + direction * plan.target_r * risk, nd),
        "hold_candles": plan.hold_candles,
        "timestamp": int(current.timestamp),
        "why": why,
    }


def detect_all(candles: Sequence[Candle]) -> list[dict[str, Any]]:
    """Every trigger that fires on the latest closed 4-hour gold candle, in priority order."""
    if not candles:
        return []
    current = candles[-1]
    if history_key(current.symbol) != "XAUUSD" or current.timeframe != SYSTEM_TIMEFRAME:
        return []
    setups = []
    for strategy, trigger in TRIGGERS:
        direction = trigger(candles)
        if direction:
            setup = build_setup(candles, strategy, direction)
            if setup is not None:
                setups.append(setup)
    return setups


def choose(
    setups: Sequence[dict[str, Any]],
    open_trades: Sequence[tuple[str, str]],
    last_signal_age: dict[str, Optional[int]],
) -> tuple[Optional[dict[str, Any]], list[str], list[tuple[dict[str, Any], str]]]:
    """Pick the first setup the system rules allow.

    open_trades: (strategy, direction) of system trades still open.
    last_signal_age: candles since each trigger last fired (None = never).
    Returns the chosen setup, the triggers that fired (their quiet period
    starts now, taken or not), and the skipped setups with the reason.
    """
    chosen: Optional[dict[str, Any]] = None
    fired: list[str] = []
    skipped: list[tuple[dict[str, Any], str]] = []
    for setup in setups:
        strategy, direction = setup["strategy"], setup["trade_direction"]
        age = last_signal_age.get(strategy)
        if age is not None and age < PLANS[strategy].quiet_candles:
            continue
        fired.append(strategy)
        same_direction = [s for s, d in open_trades if d == direction]
        if chosen is not None:
            skipped.append((setup, f"Gold system: one signal per candle ({chosen['strategy']} came first)"))
        elif strategy in same_direction:
            skipped.append((setup, f"Gold system: a {strategy} {direction} trade is already open"))
        elif len(same_direction) >= MAX_SAME_DIRECTION:
            skipped.append((setup, f"Gold system: {MAX_SAME_DIRECTION} {direction} trades already open"))
        else:
            chosen = setup
    return chosen, fired, skipped
