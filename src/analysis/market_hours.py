"""When a market is open (UTC). Spot gold and FX trade Sunday evening to Friday
evening with a short daily break; crypto never closes; US indices (cash
prices) only trade in the New York session, 09:30-16:00 New York time."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config.instruments import get_instrument

# Wide enough to cover both the summer and the winter clock change.
WEEK_OPEN_MINUTE_SUNDAY = 22 * 60 + 5
WEEK_CLOSE_MINUTE_FRIDAY = 20 * 60 + 55
DAILY_BREAK = (20 * 60 + 55, 22 * 60 + 5)
NEW_YORK = ZoneInfo("America/New_York")
CASH_OPEN_MINUTE = 9 * 60 + 30
CASH_CLOSE_MINUTE = 16 * 60


def _utc(timestamp: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)


def _new_york(timestamp: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp), tz=NEW_YORK)


def market_open(symbol: str, timestamp: int) -> bool:
    instrument = get_instrument(symbol)
    if instrument.weekend_trading:
        return True
    if instrument.cash_session:
        local = _new_york(timestamp)
        minute = local.hour * 60 + local.minute
        return local.weekday() < 5 and CASH_OPEN_MINUTE <= minute < CASH_CLOSE_MINUTE
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


def friday_close(symbol: str, timestamp: int) -> int | None:
    """Close of the Friday on or before `timestamp` (None: never closes)."""
    from datetime import timedelta

    instrument = get_instrument(symbol)
    if instrument.weekend_trading:
        return None
    if instrument.cash_session:
        local = _new_york(timestamp)
        friday = (local - timedelta(days=(local.weekday() - 4) % 7)).replace(
            hour=CASH_CLOSE_MINUTE // 60, minute=CASH_CLOSE_MINUTE % 60, second=0, microsecond=0
        )
        return int(friday.timestamp())
    moment = _utc(timestamp)
    friday = (moment - timedelta(days=(moment.weekday() - 4) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(friday.timestamp()) + WEEK_CLOSE_MINUTE_FRIDAY * 60


def minutes_to_weekly_close(symbol: str, timestamp: int) -> int | None:
    """Minutes until Friday's close for markets that shut at the weekend."""
    instrument = get_instrument(symbol)
    if instrument.weekend_trading:
        return None
    if instrument.cash_session:
        local = _new_york(timestamp)
        if local.weekday() != 4:
            return None
        remaining = CASH_CLOSE_MINUTE - (local.hour * 60 + local.minute)
        return remaining if remaining >= 0 else None
    moment = _utc(timestamp)
    if moment.weekday() != 4:
        return None
    minute = moment.hour * 60 + moment.minute
    remaining = WEEK_CLOSE_MINUTE_FRIDAY - minute
    return remaining if remaining >= 0 else None
