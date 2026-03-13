from __future__ import annotations

from typing import List

from src.domain.candle import Candle


class ATREngine:
    """Calculate Average True Range from candle closes and ranges."""

    def calculate_atr(self, candles: List[Candle], period: int = 14) -> float:
        if period <= 0 or len(candles) < (period + 1):
            return 0.0

        true_ranges: List[float] = []
        for index in range(1, len(candles)):
            current = candles[index]
            previous = candles[index - 1]
            true_range = max(
                float(current.high) - float(current.low),
                abs(float(current.high) - float(previous.close)),
                abs(float(current.low) - float(previous.close)),
            )
            true_ranges.append(float(true_range))

        if len(true_ranges) < period:
            return 0.0

        window = true_ranges[-period:]
        return float(sum(window) / period)
