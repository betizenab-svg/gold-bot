from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class SessionEngine:
    """Score trade timing against XAUUSD killzones (UTC).

    Book consensus (ICT killzones + Brooks/Traps session studies): the
    London open (07-10), NY open (12-15) and their surroundings carry the
    day's real moves; Asian hours and the late-NY drift are where signal
    quality dies.
    """

    def classify_session(self, timestamp: int) -> str:
        hour = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).hour
        if 7 <= hour < 10:
            return "LONDON_KILLZONE"
        if 12 <= hour < 15:
            return "NY_KILLZONE"
        if 10 <= hour < 12:
            return "LONDON"
        if 15 <= hour < 16:
            return "LONDON_NY_OVERLAP"
        if 16 <= hour < 18:
            return "LONDON_CLOSE"
        if 18 <= hour < 21:
            return "NEW_YORK_LATE"
        return "OFF_SESSION"

    def evaluate(self, timestamp: int) -> dict[str, Any]:
        session = self.classify_session(timestamp)
        weekday = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).weekday()

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

        # Friday after London close = weekend gap / spread risk (book consensus).
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
        hour = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).hour
        if not (12 <= hour < 16):
            return neutral
        if not isinstance(candles, list) or not candles:
            return neutral

        day_start = int(timestamp) - (int(timestamp) % 86400)
        london = [
            c
            for c in candles
            if day_start + 7 * 3600 <= int(c.timestamp) < day_start + 12 * 3600
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
