from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from src.analysis.atr import ATREngine
from src.domain.candle import Candle

NY_TZ = ZoneInfo("America/New_York")


def gold_session_start(now_ts: int) -> int:
    """Gold's trading day rolls at 17:00 New York. Returns the epoch of the
    current session's start (DST-aware)."""
    now_ny = datetime.fromtimestamp(int(now_ts), tz=timezone.utc).astimezone(NY_TZ)
    session_start_ny = now_ny.replace(hour=17, minute=0, second=0, microsecond=0)
    if now_ny.hour < 17:
        session_start_ny -= timedelta(days=1)
    return int(session_start_ny.timestamp())


class PivotPointEngine:
    """Classic floor-trader pivots from the previous GOLD session (17:00 NY
    close, BabyPips formulas): PP=(H+L+C)/3, R1=2PP-L, S1=2PP-H,
    R2=PP+(H-L), S2=PP-(H-L), R3=H+2(PP-L), S3=L-2(H-PP).
    """

    PROXIMITY_ATR_MULT = 0.3
    BONUS = 6

    def calculate_levels(self, candles: List[Candle], now_ts: int) -> Optional[dict[str, float]]:
        if not isinstance(candles, list) or not candles:
            return None

        session_start = gold_session_start(now_ts)
        prev_day = [
            c
            for c in candles
            if session_start - 86400 <= int(c.timestamp) < session_start
        ]
        if len(prev_day) < 12:
            return None

        high = max(float(c.high) for c in prev_day)
        low = min(float(c.low) for c in prev_day)
        close = float(prev_day[-1].close)

        pp = (high + low + close) / 3.0
        return {
            "PP": round(pp, 2),
            "R1": round(2 * pp - low, 2),
            "S1": round(2 * pp - high, 2),
            "R2": round(pp + (high - low), 2),
            "S2": round(pp - (high - low), 2),
            "R3": round(high + 2 * (pp - low), 2),
            "S3": round(low - 2 * (high - pp), 2),
        }

    def evaluate(
        self,
        candles: List[Candle],
        trade_direction: str,
        entry_price: Optional[float],
        now_ts: int,
    ) -> dict[str, Any]:
        neutral = {"score": 0, "note": None, "levels": None}
        if entry_price is None:
            return neutral
        levels = self.calculate_levels(candles, now_ts)
        if not levels:
            return neutral

        atr = ATREngine().calculate_atr(candles[-30:], period=14)
        tolerance = max(self.PROXIMITY_ATR_MULT * atr, 1.0)

        direction = str(trade_direction).upper()
        supportive = (
            ("S1", "S2", "S3", "PP") if direction == "LONG" else ("R1", "R2", "R3", "PP")
        )
        for name in supportive:
            level = levels[name]
            if abs(float(entry_price) - level) <= tolerance:
                return {
                    "score": self.BONUS,
                    "note": (
                        f"Entry sits at daily pivot {name} ({level:.2f}): "
                        f"objective S/R confluence (+{self.BONUS})"
                    ),
                    "levels": levels,
                }
        return {"score": 0, "note": None, "levels": levels}


def current_session_label(now_ts: Optional[int] = None) -> str:
    """Human label for dashboards (New York clock, DST-aware)."""
    from src.analysis.sessions import SessionEngine

    ts = int(now_ts) if now_ts is not None else int(datetime.now(timezone.utc).timestamp())
    session = SessionEngine().classify_session(ts)
    labels = {
        "LONDON_KILLZONE": "London Killzone",
        "NY_KILLZONE": "New York Killzone",
        "LONDON": "London",
        "LONDON_NY_OVERLAP": "London/NY Overlap",
        "LONDON_CLOSE": "London Close",
        "NEW_YORK_LATE": "Late New York",
    }
    return labels.get(session, "Off Session (Asia)")
