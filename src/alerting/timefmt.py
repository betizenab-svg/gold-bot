"""Times shown to subscribers and the owner: East Africa Time (EAT, UTC+3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

EAT = timezone(timedelta(hours=3), "EAT")


def eat(timestamp: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp), tz=EAT)


def eat_time(timestamp: int) -> str:
    """'15:30 EAT'"""
    return eat(timestamp).strftime("%H:%M EAT")


def eat_datetime(timestamp: int) -> str:
    """'Tue 6 Oct 15:30 EAT'"""
    moment = eat(timestamp)
    return f"{moment.strftime('%a')} {moment.day} {moment.strftime('%b %H:%M')} EAT"


def eat_date(timestamp: int) -> str:
    """'Tue 6 Oct 2026'"""
    moment = eat(timestamp)
    return f"{moment.strftime('%a')} {moment.day} {moment.strftime('%b %Y')}"
