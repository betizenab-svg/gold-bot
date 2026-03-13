from __future__ import annotations

from typing import List, Optional, TypedDict

from src.domain.candle import Candle


class FractalPoint(TypedDict):
    timestamp: int
    price: float


class FractalResult(TypedDict):
    swing_high: Optional[FractalPoint]
    swing_low: Optional[FractalPoint]


class FractalDetector:
    """Detect the most recent confirmed Williams fractals from ascending candles."""

    def find_fractals(self, candles: List[Candle]) -> FractalResult:
        latest_swing_high: Optional[FractalPoint] = None
        latest_swing_low: Optional[FractalPoint] = None

        for index in range(2, len(candles) - 2):
            left_two = candles[index - 2]
            left_one = candles[index - 1]
            pivot = candles[index]
            right_one = candles[index + 1]
            right_two = candles[index + 2]

            if (
                pivot.high > left_one.high
                and pivot.high > left_two.high
                and pivot.high > right_one.high
                and pivot.high > right_two.high
            ):
                latest_swing_high = {
                    "timestamp": int(pivot.timestamp),
                    "price": float(pivot.high),
                }

            if (
                pivot.low < left_one.low
                and pivot.low < left_two.low
                and pivot.low < right_one.low
                and pivot.low < right_two.low
            ):
                latest_swing_low = {
                    "timestamp": int(pivot.timestamp),
                    "price": float(pivot.low),
                }

        return {
            "swing_high": latest_swing_high,
            "swing_low": latest_swing_low,
        }
