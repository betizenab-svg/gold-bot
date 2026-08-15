from __future__ import annotations

from typing import Any, Optional

from config.settings import ENTRY_BUFFER_PTS, PIN_BAR_TAIL_RATIO
from src.domain.candle import Candle


class PinBarRejectionStrategy:
    """Detect pin-bar rejection setups around active SMC support or resistance zones.

    Book-graded validation:
    - Grade A (classic, Candlestick Bible): tail >= 66% of range, close in the
      far third, tiny opposite wick.
    - Grade B (Brooks reversal bar): tail 33-66%, dominant trend body (>=40%),
      close beyond the prior bar's close.
    - Doji bars (small mid-range body) are never signals.
    """

    CLOSE_FAR_THIRD = 0.67
    OPPOSITE_WICK_MAX = 0.20
    DOJI_BODY_MAX = 0.10
    BROOKS_TAIL_MIN = 0.33
    BROOKS_BODY_MIN = 0.40

    def __init__(
        self,
        tail_ratio: float = PIN_BAR_TAIL_RATIO,
        entry_buffer_pts: float = ENTRY_BUFFER_PTS,
        zone_proximity_usd: float = 1.00,
    ) -> None:
        self.tail_ratio = float(tail_ratio)
        self.entry_buffer_pts = float(entry_buffer_pts)
        self.zone_proximity_usd = float(zone_proximity_usd)

    def is_valid_pin_bar(
        self,
        candle: Candle,
        prev_candle: Optional[Candle] = None,
    ) -> Optional[str]:
        total_length = float(candle.high) - float(candle.low)
        if total_length == 0:
            return None

        open_price = float(candle.open)
        close_price = float(candle.close)
        body_length = abs(close_price - open_price)
        close_position = (close_price - float(candle.low)) / total_length

        # Doji veto: small mid-range body is a one-bar trading range, not a signal.
        if body_length < self.DOJI_BODY_MAX * total_length and 0.35 <= close_position <= 0.65:
            return None

        bullish_tail = min(open_price, close_price) - float(candle.low)
        bearish_tail = float(candle.high) - max(open_price, close_price)

        # Grade A: classic pin geometry.
        required_tail = total_length * self.tail_ratio
        if (
            bullish_tail >= required_tail
            and bullish_tail > body_length
            and close_position >= self.CLOSE_FAR_THIRD
            and bearish_tail <= self.OPPOSITE_WICK_MAX * total_length
        ):
            return "BULLISH"
        if (
            bearish_tail >= required_tail
            and bearish_tail > body_length
            and close_position <= (1.0 - self.CLOSE_FAR_THIRD)
            and bullish_tail <= self.OPPOSITE_WICK_MAX * total_length
        ):
            return "BEARISH"

        # Grade B: Brooks reversal bar (needs the prior close for context).
        if prev_candle is not None:
            prev_close = float(prev_candle.close)
            tail_fraction_bull = bullish_tail / total_length
            tail_fraction_bear = bearish_tail / total_length
            body_fraction = body_length / total_length

            if (
                self.BROOKS_TAIL_MIN <= tail_fraction_bull < self.tail_ratio
                and body_fraction >= self.BROOKS_BODY_MIN
                and close_price > open_price
                and close_price > prev_close
                and close_position >= self.CLOSE_FAR_THIRD
            ):
                return "BULLISH"
            if (
                self.BROOKS_TAIL_MIN <= tail_fraction_bear < self.tail_ratio
                and body_fraction >= self.BROOKS_BODY_MIN
                and close_price < open_price
                and close_price < prev_close
                and close_position <= (1.0 - self.CLOSE_FAR_THIRD)
            ):
                return "BEARISH"

        return None

    def detect_setup(
        self,
        candles: list[Candle],
        active_zones: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if not candles:
            return None

        current_candle = candles[-1]
        prev_candle = candles[-2] if len(candles) >= 2 else None
        pin_bar_bias = self.is_valid_pin_bar(current_candle, prev_candle)
        if pin_bar_bias is None:
            return None

        matched_zone = self._find_confluent_zone(
            current_candle=current_candle,
            active_zones=active_zones,
            pin_bar_bias=pin_bar_bias,
        )
        if matched_zone is None:
            return None

        if pin_bar_bias == "BULLISH":
            trade_direction = "LONG"
            entry_price = float(current_candle.high) + self.entry_buffer_pts
            sl_price = float(current_candle.low) - self.entry_buffer_pts
        else:
            trade_direction = "SHORT"
            entry_price = float(current_candle.low) - self.entry_buffer_pts
            sl_price = float(current_candle.high) + self.entry_buffer_pts

        return {
            "strategy": "PIN_BAR_REJECTION",
            "trade_direction": trade_direction,
            "order_type": "STOP",
            "entry_price": round(entry_price, 2),
            "sl_price": round(sl_price, 2),
            "zone_id": matched_zone.get("id"),
            "zone": matched_zone,
            "timestamp": int(current_candle.timestamp),
        }

    def _find_confluent_zone(
        self,
        current_candle: Candle,
        active_zones: list[dict[str, Any]],
        pin_bar_bias: str,
    ) -> Optional[dict[str, Any]]:
        probe_price = (
            float(current_candle.low) if pin_bar_bias == "BULLISH" else float(current_candle.high)
        )
        required_direction = "BULLISH" if pin_bar_bias == "BULLISH" else "BEARISH"

        for zone in active_zones:
            status = str(zone.get("status", "")).upper()
            zone_type = str(zone.get("type", "")).upper()
            if status not in {"ACTIVE", "UNMITIGATED"}:
                continue
            if required_direction not in zone_type:
                continue

            try:
                price_top = float(zone["price_top"])
                price_bottom = float(zone["price_bottom"])
            except (KeyError, TypeError, ValueError):
                continue

            zone_low = min(price_top, price_bottom)
            zone_high = max(price_top, price_bottom)
            intersects_zone = zone_low <= probe_price <= zone_high
            near_boundary = min(
                abs(probe_price - price_top),
                abs(probe_price - price_bottom),
            ) < self.zone_proximity_usd

            if intersects_zone or near_boundary:
                return zone

        return None
