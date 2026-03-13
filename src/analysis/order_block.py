from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.analysis.displacement import DisplacementEngine
from src.domain.candle import Candle


class OrderBlockScanner:
    """Detect validated order blocks around a current-pulse BOS."""

    def __init__(self) -> None:
        self.displacement_engine = DisplacementEngine()

    @staticmethod
    def _find_order_block_index(candles: List[Candle], recent_bos_type: str) -> Optional[int]:
        trend = recent_bos_type.upper()
        start_index = len(candles) - 2

        if trend == "BULLISH":
            for index in range(start_index, -1, -1):
                candle = candles[index]
                if float(candle.close) < float(candle.open):
                    return index
            return None

        if trend == "BEARISH":
            for index in range(start_index, -1, -1):
                candle = candles[index]
                if float(candle.close) > float(candle.open):
                    return index
            return None

        return None

    @staticmethod
    def _has_associated_fvg(
        recent_fvgs: List[dict[str, Any]],
        recent_bos_type: str,
        earliest_created_at: int,
    ) -> bool:
        expected_type = "FVG_BULLISH" if recent_bos_type.upper() == "BULLISH" else "FVG_BEARISH"

        for fvg in recent_fvgs:
            if fvg.get("type") != expected_type:
                continue
            if fvg.get("status") != "UNMITIGATED":
                continue

            created_at = fvg.get("created_at")
            if created_at is None:
                return True

            try:
                if int(created_at) >= earliest_created_at:
                    return True
            except (TypeError, ValueError):
                continue

        return False

    def detect_order_block(
        self,
        candles: List[Candle],
        recent_bos_type: str,
        recent_fvgs: List[dict[str, Any]],
        avg_body: float,
    ) -> Optional[Dict[str, Any]]:
        if len(candles) < 3 or avg_body <= 0:
            return None

        order_block_index = self._find_order_block_index(candles, recent_bos_type)
        if order_block_index is None or (order_block_index + 2) >= len(candles):
            return None

        following_candles = candles[order_block_index + 1 :]
        has_displacement = any(
            self.displacement_engine.detect_displacement(candle, avg_body)
            for candle in following_candles
        )
        if not has_displacement:
            return None

        earliest_fvg_timestamp = int(candles[order_block_index + 2].timestamp)
        if not self._has_associated_fvg(recent_fvgs, recent_bos_type, earliest_fvg_timestamp):
            return None

        order_block_candle = candles[order_block_index]
        zone_type = "OB_BULLISH" if recent_bos_type.upper() == "BULLISH" else "OB_BEARISH"
        return {
            "symbol": order_block_candle.symbol,
            "timeframe": order_block_candle.timeframe,
            "type": zone_type,
            "price_top": float(order_block_candle.high),
            "price_bottom": float(order_block_candle.low),
            "status": "ACTIVE",
        }
