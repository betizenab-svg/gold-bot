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
        confirmation_close: Optional[float] = None,
    ) -> bool:
        """Body-close break of the trend swing. When confirmation_close (the
        prior candle's close) is provided, both closes must clear the swing
        (book rule: a break needs two consecutive closes, one bar is a trap)."""
        swing_price = self._extract_swing_price(last_swing_point)
        if swing_price is None:
            return False

        close_price = float(current_candle.close)
        trend = current_trend.upper()

        if trend == "BULLISH":
            confirmed = confirmation_close is None or float(confirmation_close) > swing_price
            return close_price > swing_price and confirmed
        if trend == "BEARISH":
            confirmed = confirmation_close is None or float(confirmation_close) < swing_price
            return close_price < swing_price and confirmed
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
