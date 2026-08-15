from __future__ import annotations

import json
from typing import Any, List, Optional


def _parse_pivots(raw: Any) -> List[dict[str, float]]:
    if not isinstance(raw, list):
        return []
    pivots: List[dict[str, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            pivots.append(
                {"timestamp": int(item["timestamp"]), "price": float(item["price"])}
            )
        except (KeyError, TypeError, ValueError):
            continue
    return pivots


class TrendlineEngine:
    """Brooks' most important rule: no counter-trend trade until the trend
    line is broken by a body close. The line is drawn through the last two
    swing pivots on the trend side (highs in a downtrend, lows in an uptrend).
    """

    MAX_PIVOTS = 5

    @classmethod
    def update_history(
        cls,
        history_raw: Optional[str],
        latest_fractals: dict[str, Any],
    ) -> dict[str, List[dict[str, float]]]:
        """Merge the newest confirmed fractals into the rolling pivot history."""
        history: dict[str, List[dict[str, float]]] = {"highs": [], "lows": []}
        if history_raw:
            try:
                parsed = json.loads(history_raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                history["highs"] = _parse_pivots(parsed.get("highs"))
                history["lows"] = _parse_pivots(parsed.get("lows"))

        for key, side in (("swing_high", "highs"), ("swing_low", "lows")):
            pivot = latest_fractals.get(key)
            if not isinstance(pivot, dict):
                continue
            try:
                entry = {"timestamp": int(pivot["timestamp"]), "price": float(pivot["price"])}
            except (KeyError, TypeError, ValueError):
                continue
            existing = history[side]
            if existing and int(existing[-1]["timestamp"]) == entry["timestamp"]:
                continue
            existing.append(entry)
            history[side] = existing[-cls.MAX_PIVOTS :]

        return history

    @staticmethod
    def _line_value_at(
        pivots: List[dict[str, float]],
        timestamp: int,
    ) -> Optional[float]:
        if len(pivots) < 2:
            return None
        p1, p2 = pivots[-2], pivots[-1]
        ts1, ts2 = int(p1["timestamp"]), int(p2["timestamp"])
        if ts2 <= ts1:
            return None
        slope = (float(p2["price"]) - float(p1["price"])) / (ts2 - ts1)
        return float(p2["price"]) + slope * (int(timestamp) - ts2)

    def counter_trend_check(
        self,
        trade_direction: str,
        current_structure: str,
        swing_history: Optional[dict[str, Any]],
        current_close: float,
        current_timestamp: int,
    ) -> dict[str, Any]:
        neutral = {"veto": False, "note": None}
        direction = str(trade_direction).upper()
        structure = str(current_structure).upper()

        counter_trend = (direction == "LONG" and structure == "BEARISH") or (
            direction == "SHORT" and structure == "BULLISH"
        )
        if not counter_trend:
            return neutral
        if not isinstance(swing_history, dict):
            return neutral

        if direction == "LONG":
            pivots = _parse_pivots(swing_history.get("highs"))
            line_value = self._line_value_at(pivots, current_timestamp)
            if line_value is None:
                return neutral
            if float(current_close) > line_value:
                return {
                    "veto": False,
                    "note": "Counter-trend long permitted: downtrend line already broken",
                }
            return {
                "veto": True,
                "note": "Counter-trend long without a downtrend-line break: vetoed",
            }

        pivots = _parse_pivots(swing_history.get("lows"))
        line_value = self._line_value_at(pivots, current_timestamp)
        if line_value is None:
            return neutral
        if float(current_close) < line_value:
            return {
                "veto": False,
                "note": "Counter-trend short permitted: uptrend line already broken",
            }
        return {
            "veto": True,
            "note": "Counter-trend short without an uptrend-line break: vetoed",
        }
