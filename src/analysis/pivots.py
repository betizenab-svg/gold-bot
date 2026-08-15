from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

from src.analysis.atr import ATREngine
from src.domain.candle import Candle


class PivotPointEngine:
    """Classic floor-trader pivots from the previous UTC day (BabyPips formulas):
    PP=(H+L+C)/3, R1=2PP-L, S1=2PP-H, R2=PP+(H-L), S2=PP-(H-L),
    R3=H+2(PP-L), S3=L-2(H-PP). Entries near a supportive pivot score a bonus.
    """

    PROXIMITY_ATR_MULT = 0.3
    BONUS = 6

    def calculate_levels(self, candles: List[Candle], now_ts: int) -> Optional[dict[str, float]]:
        if not isinstance(candles, list) or not candles:
            return None

        day_start = int(now_ts) - (int(now_ts) % 86400)
        prev_day = [
            c for c in candles if day_start - 86400 <= int(c.timestamp) < day_start
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
    """Human label for dashboards."""
    ts = int(now_ts) if now_ts is not None else int(datetime.now(timezone.utc).timestamp())
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    if 7 <= hour < 10:
        return "London Killzone"
    if 12 <= hour < 15:
        return "New York Killzone"
    if 10 <= hour < 12:
        return "London"
    if 15 <= hour < 16:
        return "London/NY Overlap"
    if 16 <= hour < 18:
        return "London Close"
    if 18 <= hour < 21:
        return "Late New York"
    return "Off Session (Asia)"
