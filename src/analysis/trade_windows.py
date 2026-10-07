"""How long a signal may wait for its entry, and how long an open trade may
run before the time stop, for each market's chart timeframe.

The 90-minute entry window and 24-hour time stop suit 5-minute charts; slower
swing charts get at least 3 candles to fill and 24 candles to work out."""

from __future__ import annotations

from config import settings
from config.instruments import get_instrument
from src.strategies.gold_system import hold_candles

MIN_PENDING_CANDLES = 3
MIN_HOLD_CANDLES = 24


def candle_seconds(symbol: str | None) -> int:
    timeframe = get_instrument(symbol).signal_timeframe or settings.SIGNAL_TIMEFRAME
    return int(settings.TIMEFRAME_SECONDS.get(timeframe, 300))


def pending_window_seconds(symbol: str | None) -> int:
    return max(int(settings.SIGNAL_EXPIRY_MINUTES) * 60, MIN_PENDING_CANDLES * candle_seconds(symbol))


def max_hold_seconds(symbol: str | None, strategy: object = None) -> int:
    """Gold system trades get exactly their tested time limit, counted from
    the close of the signal candle (the signal is stamped with its open)."""
    fixed = hold_candles(strategy)
    if fixed:
        return (fixed + 1) * candle_seconds(symbol)
    return max(int(settings.ACTIVE_MAX_HOLD_HOURS) * 3600, MIN_HOLD_CANDLES * candle_seconds(symbol))
