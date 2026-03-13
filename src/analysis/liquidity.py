from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.domain.candle import Candle


class LiquiditySweepDetector:
    """Detect liquidity sweeps using wick breaches, close rejections, and volume spikes."""

    def calculate_average_volume(self, candles: List[Candle], period: int = 14) -> float:
        if period <= 0 or len(candles) < period:
            return 0.0

        window = candles[-period:]
        volumes = [float(candle.volume) for candle in window]
        return float(sum(volumes) / period)

    def detect_sweep(
        self,
        current_candle: Candle,
        avg_volume: float,
        last_swing_high: Optional[float],
        last_swing_low: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        if avg_volume <= 0:
            return None

        if float(current_candle.volume) <= (1.2 * float(avg_volume)):
            return None

        if last_swing_low is not None:
            if float(current_candle.low) < float(last_swing_low) and float(current_candle.close) > float(last_swing_low):
                return {
                    "type": "LIQUIDITY_SWEEP_LONG",
                    "sweep_price": float(current_candle.low),
                    "timestamp": int(current_candle.timestamp),
                }

        if last_swing_high is not None:
            if float(current_candle.high) > float(last_swing_high) and float(current_candle.close) < float(last_swing_high):
                return {
                    "type": "LIQUIDITY_SWEEP_SHORT",
                    "sweep_price": float(current_candle.high),
                    "timestamp": int(current_candle.timestamp),
                }

        return None
