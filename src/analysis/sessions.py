from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


def ny_hour(timestamp: int) -> int:
    """Gold's sessions follow New York clock (DST-aware), not fixed UTC."""
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(NY_TZ).hour


class SessionEngine:
    """Score trade timing against XAUUSD killzones (New York time, DST-aware).

    Book consensus (ICT killzones + Brooks/Traps session studies): London
    killzone 02:00-05:00 NY, NY killzone 07:00-10:00 NY carry the day's real
    moves; the Asian hours and the late-NY drift are where signal quality dies.
    """

    def classify_session(self, timestamp: int) -> str:
        hour = ny_hour(timestamp)
        if 2 <= hour < 5:
            return "LONDON_KILLZONE"
        if 7 <= hour < 10:
            return "NY_KILLZONE"
        if 5 <= hour < 7:
            return "LONDON"
        if 10 <= hour < 11:
            return "LONDON_NY_OVERLAP"
        if 11 <= hour < 13:
            return "LONDON_CLOSE"
        if 13 <= hour < 16:
            return "NEW_YORK_LATE"
        return "OFF_SESSION"

    def evaluate(self, timestamp: int) -> dict[str, Any]:
        session = self.classify_session(timestamp)
        ny_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(NY_TZ)
        weekday = ny_time.weekday()

        if session in {"LONDON_KILLZONE", "NY_KILLZONE"}:
            score = 10
            note = f"{session.replace('_', ' ').title()}: prime liquidity window (+10)"
        elif session in {"LONDON", "LONDON_NY_OVERLAP", "LONDON_CLOSE"}:
            score = 5
            note = f"{session.replace('_', ' ').title()}: good liquidity (+5)"
        elif session == "NEW_YORK_LATE":
            score = 0
            note = "Late NY: fading liquidity (neutral)"
        else:
            score = -10
            note = "Off-session: thin liquidity (-10)"

        # Friday afternoon NY = weekend gap / spread risk (book consensus).
        if weekday == 4 and session in {"NEW_YORK_LATE", "OFF_SESSION"}:
            score -= 5
            note += " | Late Friday: weekend risk (-5)"

        return {"session": session, "score": int(score), "note": note}

    def london_continuation(
        self,
        candles: Any,
        trade_direction: str,
        timestamp: int,
    ) -> dict[str, Any]:
        """NY signals aligned with London's net direction get a bonus (MMM:
        'the direction taken in London often continues in New York')."""
        neutral = {"score": 0, "note": None}
        hour = ny_hour(timestamp)
        if not (7 <= hour < 11):
            return neutral
        if not isinstance(candles, list) or not candles:
            return neutral

        # London window = 02:00-07:00 NY of the same NY calendar day.
        signal_day = (
            datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(NY_TZ).date()
        )
        london = [
            c
            for c in candles
            if (
                lambda ny: ny.date() == signal_day and 2 <= ny.hour < 7
            )(datetime.fromtimestamp(int(c.timestamp), tz=timezone.utc).astimezone(NY_TZ))
        ]
        if len(london) < 12:
            return neutral

        net = float(london[-1].close) - float(london[0].open)
        if abs(net) < 0.5:
            return neutral
        london_direction = "LONG" if net > 0 else "SHORT"

        if str(trade_direction).upper() == london_direction:
            return {
                "score": 5,
                "note": f"NY signal continues London's {london_direction} move (+5)",
            }
        return {
            "score": -5,
            "note": f"NY signal fights London's {london_direction} move (-5)",
        }
