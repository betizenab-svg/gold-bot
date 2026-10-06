"""Strong one-way days: on a day that has run hard in one direction, signals
against that direction are blocked (the blocked ideas are still followed, so
the history tests and the filter report card show whether this helps)."""

from __future__ import annotations

from typing import Optional

from config.settings import TIMEFRAME_SECONDS
from src.domain.candle import Candle

MIN_HOURS_INTO_DAY = 2
NET_SHARE_OF_RANGE = 0.6  # the day's move is mostly one way
CLOSE_NEAR_EXTREME = 0.25  # and price is still near that extreme
RANGE_VS_YESTERDAY = 1.0  # on a day already as wide as all of yesterday


def trend_day_direction(candles: list[Candle]) -> Optional[str]:
    """'UP', 'DOWN' or None for the UTC day of the newest candle."""
    if not candles:
        return None
    current = candles[-1]
    step = int(TIMEFRAME_SECONDS.get(current.timeframe, 300))
    day_start = int(current.timestamp) - int(current.timestamp) % 86400
    today = [c for c in candles if int(c.timestamp) >= day_start]
    if len(today) * step < MIN_HOURS_INTO_DAY * 3600:
        return None
    yesterday = [c for c in candles if day_start - 86400 <= int(c.timestamp) < day_start]
    if not yesterday:
        return None
    high = max(float(c.high) for c in today)
    low = min(float(c.low) for c in today)
    day_range = high - low
    yesterday_range = max(float(c.high) for c in yesterday) - min(float(c.low) for c in yesterday)
    if day_range <= 0 or yesterday_range <= 0 or day_range < RANGE_VS_YESTERDAY * yesterday_range:
        return None
    net = float(current.close) - float(today[0].open)
    if abs(net) < NET_SHARE_OF_RANGE * day_range:
        return None
    if net > 0 and high - float(current.close) <= CLOSE_NEAR_EXTREME * day_range:
        return "UP"
    if net < 0 and float(current.close) - low <= CLOSE_NEAR_EXTREME * day_range:
        return "DOWN"
    return None


def against_trend_day(candles: list[Candle], direction: str) -> Optional[str]:
    """Reason text when `direction` fights a strong one-way day, else None."""
    trend = trend_day_direction(candles)
    if trend == "UP" and str(direction).upper() == "SHORT":
        return "One-way day: price has run up all day; no sells against it"
    if trend == "DOWN" and str(direction).upper() == "LONG":
        return "One-way day: price has run down all day; no buys against it"
    return None
