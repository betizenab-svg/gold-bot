from __future__ import annotations

from typing import Any, List

from src.analysis.atr import ATREngine
from src.domain.candle import Candle


class VolatilityRegimeEngine:
    """Classify current volatility versus its recent baseline.

    Ratio = ATR(short window) / ATR(baseline window). EXTREME regimes veto new
    entries because spreads and slippage destroy tight-risk setups.
    """

    COMPRESSED_MAX = 0.65
    NORMAL_MAX = 1.60
    ELEVATED_MAX = 2.50

    def __init__(self, short_period: int = 14, baseline_period: int = 60) -> None:
        self.short_period = int(short_period)
        self.baseline_period = int(baseline_period)
        self.atr_engine = ATREngine()

    def evaluate(self, candles: List[Candle], order_type: str = "LIMIT") -> dict[str, Any]:
        neutral = {
            "state": "UNKNOWN",
            "ratio": 1.0,
            "score": 0,
            "veto": False,
            "note": "Volatility regime: insufficient history (neutral)",
        }
        required = self.baseline_period + self.short_period + 1
        if not isinstance(candles, list) or len(candles) < required:
            return neutral

        current_atr = self.atr_engine.calculate_atr(candles, period=self.short_period)
        baseline_atr = self.atr_engine.calculate_atr(candles, period=self.baseline_period)
        if current_atr <= 0 or baseline_atr <= 0:
            return neutral

        ratio = float(current_atr) / float(baseline_atr)
        is_stop = str(order_type).upper() == "STOP"

        if ratio > self.ELEVATED_MAX:
            return {
                "state": "EXTREME",
                "ratio": round(ratio, 2),
                "score": 0,
                "veto": True,
                "note": f"EXTREME volatility (x{ratio:.2f} baseline): entry vetoed",
            }
        if ratio > self.NORMAL_MAX:
            score = 8 if is_stop else -4
            note = (
                f"Elevated volatility (x{ratio:.2f}): favors breakout entries"
                if is_stop
                else f"Elevated volatility (x{ratio:.2f}): penalizes passive limit fills"
            )
            state = "ELEVATED"
        elif ratio < self.COMPRESSED_MAX:
            score = -5
            note = f"Compressed volatility (x{ratio:.2f}): breakouts prone to failure (-5)"
            state = "COMPRESSED"
        else:
            score = 5
            note = f"Normal volatility (x{ratio:.2f}): tradable conditions (+5)"
            state = "NORMAL"

        return {
            "state": state,
            "ratio": round(ratio, 2),
            "score": int(score),
            "veto": False,
            "note": note,
        }
