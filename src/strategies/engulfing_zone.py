from __future__ import annotations

from typing import Any, Optional

from config.settings import ENTRY_BUFFER_PTS
from src.analysis.momentum import calculate_ema
from src.domain.candle import Candle


class EngulfingZoneStrategy:
    """Engulfing bar at an SMC zone — the books' highest-consensus reversal
    trigger (Nison's 3 criteria via the Candlestick Bible).

    Valid only when: (a) a definable prior leg exists (EMA21 context),
    (b) the signal body fully engulfs the prior body, (c) bodies are opposite
    colors, and (d) the bar forms at an active zone. STOP entry beyond the
    engulfing extreme, stop loss beyond the other end.
    """

    ZONE_PROXIMITY_USD = 1.00
    MIN_BODY_RATIO = 0.5  # engulfing body must dominate its own range

    def __init__(self, entry_buffer_pts: float = ENTRY_BUFFER_PTS) -> None:
        self.entry_buffer_pts = float(entry_buffer_pts)

    def _is_engulfing(self, prev: Candle, current: Candle) -> Optional[str]:
        prev_open, prev_close = float(prev.open), float(prev.close)
        cur_open, cur_close = float(current.open), float(current.close)

        prev_body_top = max(prev_open, prev_close)
        prev_body_bottom = min(prev_open, prev_close)
        cur_body_top = max(cur_open, cur_close)
        cur_body_bottom = min(cur_open, cur_close)

        cur_range = float(current.high) - float(current.low)
        if cur_range <= 0:
            return None
        if (cur_body_top - cur_body_bottom) < self.MIN_BODY_RATIO * cur_range:
            return None
        if prev_body_top == prev_body_bottom:
            return None

        engulfs = cur_body_top >= prev_body_top and cur_body_bottom <= prev_body_bottom
        if not engulfs:
            return None

        prev_bullish = prev_close > prev_open
        cur_bullish = cur_close > cur_open
        if prev_bullish == cur_bullish:
            return None
        return "BULLISH" if cur_bullish else "BEARISH"

    def detect_setup(
        self,
        candles: list[Candle],
        active_zones: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if len(candles) < 22 or not active_zones:
            return None

        current = candles[-1]
        prev = candles[-2]
        bias = self._is_engulfing(prev, current)
        if bias is None:
            return None

        # Nison criterion (a): a definable prior leg — price must have been on
        # the opposite side of EMA21, i.e. an engulfing REVERSAL, not chop.
        closes = [float(c.close) for c in candles]
        ema21 = calculate_ema(closes[:-1], 21)
        if ema21 is None:
            return None
        prior_close = float(prev.close)
        if bias == "BULLISH" and prior_close > ema21:
            return None
        if bias == "BEARISH" and prior_close < ema21:
            return None

        matched_zone = self._find_zone(current, active_zones, bias)
        if matched_zone is None:
            return None

        if bias == "BULLISH":
            trade_direction = "LONG"
            entry_price = float(current.high) + self.entry_buffer_pts
            sl_price = float(current.low) - self.entry_buffer_pts
        else:
            trade_direction = "SHORT"
            entry_price = float(current.low) - self.entry_buffer_pts
            sl_price = float(current.high) + self.entry_buffer_pts

        return {
            "strategy": "ENGULFING_ZONE",
            "trade_direction": trade_direction,
            "order_type": "STOP",
            "entry_price": round(entry_price, 2),
            "sl_price": round(sl_price, 2),
            "zone_id": matched_zone.get("id"),
            "zone": matched_zone,
            "timestamp": int(current.timestamp),
        }

    def _find_zone(
        self,
        current: Candle,
        active_zones: list[dict[str, Any]],
        bias: str,
    ) -> Optional[dict[str, Any]]:
        probe = float(current.low) if bias == "BULLISH" else float(current.high)
        wanted = "BULLISH" if bias == "BULLISH" else "BEARISH"

        for zone in active_zones:
            if str(zone.get("status", "")).upper() not in {"ACTIVE", "UNMITIGATED"}:
                continue
            if wanted not in str(zone.get("type", "")).upper():
                continue
            try:
                top = float(zone["price_top"])
                bottom = float(zone["price_bottom"])
            except (KeyError, TypeError, ValueError):
                continue
            low, high = min(top, bottom), max(top, bottom)
            if low <= probe <= high or min(abs(probe - top), abs(probe - bottom)) < self.ZONE_PROXIMITY_USD:
                return zone
        return None
