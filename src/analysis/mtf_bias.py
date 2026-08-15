from __future__ import annotations

from typing import Any, List, Optional

from src.analysis.momentum import calculate_ema
from src.domain.candle import Candle


class MultiTimeframeBiasEngine:
    """Check EMA trend agreement on the signal timeframe and one aggregated
    higher timeframe. Trading against both timeframes is vetoed."""

    FAST_EMA = 9
    SLOW_EMA = 21
    AGGREGATION_FACTOR = 3

    def _aggregate_closes(self, candles: List[Candle], factor: int) -> List[float]:
        closes: List[float] = []
        total = len(candles)
        # Walk backwards in fixed buckets so the newest bucket is always complete.
        end = total
        while end - factor >= 0:
            bucket = candles[end - factor : end]
            closes.append(float(bucket[-1].close))
            end -= factor
        closes.reverse()
        return closes

    def _trend_of(self, closes: List[float]) -> Optional[str]:
        fast = calculate_ema(closes, self.FAST_EMA)
        slow = calculate_ema(closes, self.SLOW_EMA)
        if fast is None or slow is None:
            return None
        if fast > slow:
            return "BULLISH"
        if fast < slow:
            return "BEARISH"
        return "FLAT"

    def evaluate(self, candles: List[Candle], trade_direction: str) -> dict[str, Any]:
        neutral = {
            "signal_tf_trend": None,
            "higher_tf_trend": None,
            "score": 0,
            "veto": False,
            "note": "MTF bias: insufficient history (neutral)",
        }
        min_needed = self.SLOW_EMA * self.AGGREGATION_FACTOR + self.AGGREGATION_FACTOR
        if not isinstance(candles, list) or len(candles) < min_needed:
            return neutral

        direction = str(trade_direction).upper()
        wanted = "BULLISH" if direction == "LONG" else "BEARISH"
        opposed = "BEARISH" if direction == "LONG" else "BULLISH"

        signal_tf_closes = [float(candle.close) for candle in candles]
        higher_tf_closes = self._aggregate_closes(candles, self.AGGREGATION_FACTOR)

        signal_trend = self._trend_of(signal_tf_closes)
        higher_trend = self._trend_of(higher_tf_closes)
        if signal_trend is None or higher_trend is None:
            return neutral

        aligned = sum(1 for trend in (signal_trend, higher_trend) if trend == wanted)
        both_opposed = signal_trend == opposed and higher_trend == opposed

        if both_opposed:
            return {
                "signal_tf_trend": signal_trend,
                "higher_tf_trend": higher_trend,
                "score": 0,
                "veto": True,
                "note": "Both timeframes trend against the trade: vetoed",
            }
        if aligned == 2:
            score = 15
            note = "Signal + higher timeframe aligned with trade (+15)"
        elif aligned == 1:
            score = 5
            note = "One timeframe aligned with trade (+5)"
        else:
            score = 0
            note = "MTF bias: mixed/flat (neutral)"

        return {
            "signal_tf_trend": signal_trend,
            "higher_tf_trend": higher_trend,
            "score": int(score),
            "veto": False,
            "note": note,
        }
