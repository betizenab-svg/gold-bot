"""When a market is open (UTC). Spot gold and FX trade Sunday evening to Friday
evening with a short daily break; crypto never closes."""

from __future__ import annotations

from datetime import datetime, timezone

from config.instruments import get_instrument

# Wide enough to cover both the summer and the winter clock change.
WEEK_OPEN_MINUTE_SUNDAY = 22 * 60 + 5
WEEK_CLOSE_MINUTE_FRIDAY = 20 * 60 + 55
DAILY_BREAK = (20 * 60 + 55, 22 * 60 + 5)


def _utc(timestamp: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)


def market_open(symbol: str, timestamp: int) -> bool:
    if get_instrument(symbol).weekend_trading:
        return True
    moment = _utc(timestamp)
    minute = moment.hour * 60 + moment.minute
    weekday = moment.weekday()
    if weekday == 5:
        return False
    if weekday == 6:
        return minute >= WEEK_OPEN_MINUTE_SUNDAY
    if weekday == 4 and minute >= WEEK_CLOSE_MINUTE_FRIDAY:
        return False
    return not (DAILY_BREAK[0] <= minute < DAILY_BREAK[1])


def minutes_to_weekly_close(symbol: str, timestamp: int) -> int | None:
    """Minutes until Friday's close for markets that shut at the weekend."""
    if get_instrument(symbol).weekend_trading:
        return None
    moment = _utc(timestamp)
    if moment.weekday() != 4:
        return None
    minute = moment.hour * 60 + moment.minute
    remaining = WEEK_CLOSE_MINUTE_FRIDAY - minute
    return remaining if remaining >= 0 else None
