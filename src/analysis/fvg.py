from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.domain.candle import Candle


class FVGScanner:
    """Detect Fair Value Gaps from the latest three closed candles."""

    def detect_fvg(
        self,
        candles: List[Candle],
        current_atr: float,
    ) -> Optional[Dict[str, Any]]:
        if len(candles) < 3 or current_atr <= 0:
            return None

        candle_1, _, candle_3 = candles[-3:]
        min_gap_size = 0.5 * float(current_atr)

        if float(candle_1.high) < float(candle_3.low):
            gap_size = float(candle_3.low) - float(candle_1.high)
            if gap_size <= min_gap_size:
                return None
            return {
                "symbol": candle_3.symbol,
                "timeframe": candle_3.timeframe,
                "type": "FVG_BULLISH",
                "price_top": float(candle_3.low),
                "price_bottom": float(candle_1.high),
                "status": "UNMITIGATED",
            }

        if float(candle_1.low) > float(candle_3.high):
            gap_size = float(candle_1.low) - float(candle_3.high)
            if gap_size <= min_gap_size:
                return None
            return {
                "symbol": candle_3.symbol,
                "timeframe": candle_3.timeframe,
                "type": "FVG_BEARISH",
                "price_top": float(candle_1.low),
                "price_bottom": float(candle_3.high),
                "status": "UNMITIGATED",
            }

        return None
