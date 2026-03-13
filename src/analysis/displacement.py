from __future__ import annotations

from typing import List

from src.domain.candle import Candle


class DisplacementEngine:
    """Measure candle-body displacement using simple moving averages."""

    def calculate_average_body(self, candles: List[Candle], period: int = 14) -> float:
        if period <= 0 or len(candles) < period:
            return 0.0

        window = candles[-period:]
        body_sizes = [abs(float(candle.close) - float(candle.open)) for candle in window]
        return float(sum(body_sizes) / period)

    def detect_displacement(self, current_candle: Candle, avg_body: float) -> bool:
        if avg_body <= 0:
            return False

        body_size = abs(float(current_candle.close) - float(current_candle.open))
        return body_size > (1.5 * float(avg_body))
