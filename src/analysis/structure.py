from __future__ import annotations

from typing import Any, Mapping, Optional

from src.domain.candle import Candle


class MarketStructureEngine:
    """Stateless BOS and CHOCH detection using candle body closes only."""

    @staticmethod
    def _extract_swing_price(
        swing_point: Optional[Mapping[str, Any]],
    ) -> Optional[float]:
        if not swing_point:
            return None

        raw_price = swing_point.get("price")
        if raw_price is None:
            return None

        try:
            return float(raw_price)
        except (TypeError, ValueError):
            return None

    def detect_bos(
        self,
        current_candle: Candle,
        last_swing_point: Optional[Mapping[str, Any]],
        current_trend: str,
    ) -> bool:
        swing_price = self._extract_swing_price(last_swing_point)
        if swing_price is None:
            return False

        close_price = float(current_candle.close)
        trend = current_trend.upper()

        if trend == "BULLISH":
            return close_price > swing_price
        if trend == "BEARISH":
            return close_price < swing_price
        return False

    def detect_choch(
        self,
        current_candle: Candle,
        last_counter_trend_swing: Optional[Mapping[str, Any]],
        current_trend: str,
    ) -> Optional[str]:
        swing_price = self._extract_swing_price(last_counter_trend_swing)
        if swing_price is None:
            return None

        close_price = float(current_candle.close)
        trend = current_trend.upper()

        if trend == "BULLISH" and close_price < swing_price:
            return "BEARISH"
        if trend == "BEARISH" and close_price > swing_price:
            return "BULLISH"
        return None
