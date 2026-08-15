from __future__ import annotations

from typing import Any, List

from src.analysis.atr import ATREngine
from src.domain.candle import Candle


def _overlap_ratio(candle_a: Candle, candle_b: Candle) -> float:
    high = min(float(candle_a.high), float(candle_b.high))
    low = max(float(candle_a.low), float(candle_b.low))
    overlap = max(0.0, high - low)
    range_b = float(candle_b.high) - float(candle_b.low)
    if range_b <= 0:
        return 1.0
    return overlap / range_b


class MarketStateEngine:
    """Book-derived no-trade vetoes: barbwire chop, climax exhaustion,
    oversized breakout bars (Brooks / Price Action Traps consensus)."""

    BARBWIRE_BARS = 3
    BARBWIRE_OVERLAP = 0.50
    CLIMAX_BAR_ATR_MULT = 1.5
    CLIMAX_CONSECUTIVE = 3
    GIANT_BAR_ATR_MULT = 2.25
    # MMM: a day rarely travels beyond ~2-3x the Asian range; late continuation
    # entries into an already-extended day are chasing.
    DAY_EXTENSION_MULT = 2.5
    MIN_TODAY_CANDLES = 24
    MIN_ASIAN_CANDLES = 24

    def __init__(self) -> None:
        self.atr_engine = ATREngine()

    def evaluate(
        self,
        candles: List[Candle],
        trade_direction: str,
        order_type: str = "LIMIT",
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"veto": False, "score": 0, "notes": []}
        if not isinstance(candles, list) or len(candles) < self.BARBWIRE_BARS + 1:
            result["notes"].append("Market state: insufficient history (neutral)")
            return result

        notes: List[str] = []
        direction = str(trade_direction).upper()
        atr = self.atr_engine.calculate_atr(candles[-30:], period=14)

        # Barbwire: 3+ recent bars mutually overlapping = tight range. Brooks:
        # no STOP (breakout) entries inside it; fading the edges with LIMIT
        # orders is permitted but penalized.
        recent = candles[-self.BARBWIRE_BARS :]
        overlapping_pairs = 0
        for index in range(1, len(recent)):
            if _overlap_ratio(recent[index - 1], recent[index]) >= self.BARBWIRE_OVERLAP:
                overlapping_pairs += 1
        if overlapping_pairs >= self.BARBWIRE_BARS - 1:
            if str(order_type).upper() == "STOP":
                result["veto"] = True
                notes.append("Barbwire: tight overlapping range — breakout entries vetoed")
                result["notes"] = notes
                return result
            result["score"] = -10
            notes.append("Barbwire: tight overlapping range — limit entry penalized (-10)")

        if atr > 0:
            # Climax exhaustion: consecutive outsized same-direction bars.
            climax_run = 0
            run_direction = None
            for candle in candles[-self.CLIMAX_CONSECUTIVE :]:
                bar_range = float(candle.high) - float(candle.low)
                bar_dir = "UP" if float(candle.close) >= float(candle.open) else "DOWN"
                if bar_range >= self.CLIMAX_BAR_ATR_MULT * atr:
                    if run_direction in (None, bar_dir):
                        run_direction = bar_dir
                        climax_run += 1
                        continue
                climax_run = 0
                run_direction = None

            with_trend = (direction == "LONG" and run_direction == "UP") or (
                direction == "SHORT" and run_direction == "DOWN"
            )
            if climax_run >= self.CLIMAX_CONSECUTIVE and with_trend:
                result["veto"] = True
                notes.append(
                    f"Climax exhaustion: {climax_run} consecutive oversized bars — "
                    "with-trend entry vetoed"
                )
                result["notes"] = notes
                return result

            # Giant single bar: breakout STOP entries after a >2.25x ATR bar are traps.
            last_range = float(candles[-1].high) - float(candles[-1].low)
            if str(order_type).upper() == "STOP" and last_range > self.GIANT_BAR_ATR_MULT * atr:
                result["veto"] = True
                notes.append(
                    f"Giant bar ({last_range / atr:.1f}x ATR): breakout entry vetoed as likely trap"
                )
                result["notes"] = notes
                return result

        extension_note = self._day_extension_veto(candles, direction)
        if extension_note is not None:
            result["veto"] = True
            notes.append(extension_note)
            result["notes"] = notes
            return result

        if not notes:
            notes.append("Market state: clean (no chop/climax veto)")
        result["notes"] = notes
        return result

    def _day_extension_veto(self, candles: List[Candle], direction: str) -> str | None:
        last_ts = int(candles[-1].timestamp)
        day_start = last_ts - (last_ts % 86400)

        today = [c for c in candles if int(c.timestamp) >= day_start]
        asian = [
            c
            for c in candles
            if day_start - 3600 <= int(c.timestamp) < day_start + (5 * 3600)
        ]
        if len(today) < self.MIN_TODAY_CANDLES or len(asian) < self.MIN_ASIAN_CANDLES:
            return None

        day_range = max(float(c.high) for c in today) - min(float(c.low) for c in today)
        asian_range = max(float(c.high) for c in asian) - min(float(c.low) for c in asian)
        if asian_range <= 0 or day_range <= self.DAY_EXTENSION_MULT * asian_range:
            return None

        day_open = float(today[0].open)
        last_close = float(candles[-1].close)
        up_day = last_close >= day_open
        continuation = (direction == "LONG" and up_day) or (direction == "SHORT" and not up_day)
        if not continuation:
            return None
        return (
            f"Day already extended {day_range / asian_range:.1f}x the Asian range — "
            "continuation entry vetoed"
        )
