"""Session range breakouts (new strategies; they start in trial).

Opening-range breakout, London and New York: the first 30 minutes after the
session opens set a range. The first candle that CLOSES beyond it within the
next two hours is the signal. The entry waits for a retest of the broken edge
(limit order); the stop sits on the far side of the range, or at its middle
when the range is wide.

Asian-range breakout, gold only: the 00:00-06:00 UTC range, and the first
close beyond it between 06:00 and 10:00 UTC. Only the breakout is traded,
never a move back into the range (the knowledge base rules that out).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.instruments import get_instrument, history_key
from config.settings import TIMEFRAME_SECONDS
from src.domain.candle import Candle

LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
FAST_TIMEFRAMES = {"M1", "M5", "M15"}


def _atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    ranges = []
    for previous, current in zip(candles[-period - 1:-1], candles[-period:]):
        ranges.append(
            max(
                float(current.high) - float(current.low),
                abs(float(current.high) - float(previous.close)),
                abs(float(current.low) - float(previous.close)),
            )
        )
    return sum(ranges) / len(ranges) if ranges else 0.0


def _session_open(timestamp: int, zone: ZoneInfo, hour: int, minute: int) -> int:
    local_day = datetime.fromtimestamp(int(timestamp), tz=zone)
    opening = local_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(opening.timestamp())


def range_breakout(
    candles: list[Candle],
    range_start: int,
    range_end: int,
    window_end: int,
    strategy: str,
    trigger: str,
    wide_range_atr: float,
) -> Optional[dict[str, Any]]:
    """First close beyond [range_start, range_end) between range_end and window_end."""
    current = candles[-1]
    step = int(TIMEFRAME_SECONDS.get(current.timeframe, 300))
    if not (range_end <= int(current.timestamp) and int(current.timestamp) + step <= window_end):
        return None
    in_range = [c for c in candles if range_start <= int(c.timestamp) < range_end]
    expected = max(1, (range_end - range_start) // step)
    if len(in_range) < max(2, int(expected * 0.6)):
        return None
    high = max(float(c.high) for c in in_range)
    low = min(float(c.low) for c in in_range)
    width = high - low
    atr = _atr([c for c in candles if int(c.timestamp) < int(current.timestamp)] or candles)
    if width <= 0 or atr <= 0 or width < atr:
        return None

    close = float(current.close)
    if close > high:
        direction = "LONG"
    elif close < low:
        direction = "SHORT"
    else:
        return None
    earlier = [c for c in candles if range_end <= int(c.timestamp) < int(current.timestamp)]
    if direction == "LONG" and any(float(c.close) > high for c in earlier):
        return None  # only the first breakout
    if direction == "SHORT" and any(float(c.close) < low for c in earlier):
        return None

    middle = (high + low) / 2.0
    wide = width > wide_range_atr * atr
    nd = get_instrument(current.symbol).price_decimals
    if direction == "LONG":
        entry, stop = high, (middle if wide else low)
    else:
        entry, stop = low, (middle if wide else high)
    return {
        "symbol": current.symbol,
        "timeframe": current.timeframe,
        "strategy": strategy,
        "trade_direction": direction,
        "order_type": "LIMIT",
        "trigger": trigger,
        "entry_price": round(entry, nd),
        "sl_price": round(stop, nd),
        "timestamp": int(current.timestamp),
        "range_high": round(high, nd),
        "range_low": round(low, nd),
    }


class OpeningRangeBreakoutStrategy:
    STRATEGY = "OPENING_RANGE_BREAKOUT"
    RANGE_MINUTES = 30
    WINDOW_MINUTES = 120
    WIDE_RANGE_ATR = 3.0

    def detect_setup(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        if len(candles) < 20:
            return None
        current = candles[-1]
        instrument = get_instrument(current.symbol)
        if current.timeframe not in FAST_TIMEFRAMES or instrument.weekend_trading:
            return None
        sessions = [("NY_ORB", NEW_YORK, 9, 30)]
        if not instrument.cash_session:
            sessions.insert(0, ("LONDON_ORB", LONDON, 8, 0))
        for trigger, zone, hour, minute in sessions:
            opening = _session_open(int(current.timestamp), zone, hour, minute)
            setup = range_breakout(
                candles,
                opening,
                opening + self.RANGE_MINUTES * 60,
                opening + (self.RANGE_MINUTES + self.WINDOW_MINUTES) * 60,
                self.STRATEGY,
                trigger,
                self.WIDE_RANGE_ATR,
            )
            if setup is not None:
                return setup
        return None


class AsianRangeBreakoutStrategy:
    STRATEGY = "ASIAN_RANGE_BREAKOUT"
    RANGE_HOURS = (0, 6)
    WINDOW_END_HOUR = 10
    WIDE_RANGE_ATR = 4.0

    def detect_setup(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        if len(candles) < 20:
            return None
        current = candles[-1]
        if history_key(current.symbol) != "XAUUSD" or current.timeframe not in FAST_TIMEFRAMES:
            return None
        moment = datetime.fromtimestamp(int(current.timestamp), tz=timezone.utc)
        day = int(moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        return range_breakout(
            candles,
            day + self.RANGE_HOURS[0] * 3600,
            day + self.RANGE_HOURS[1] * 3600,
            day + self.WINDOW_END_HOUR * 3600,
            self.STRATEGY,
            "ASIAN_RANGE",
            self.WIDE_RANGE_ATR,
        )
