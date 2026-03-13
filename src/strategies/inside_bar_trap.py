from __future__ import annotations

from typing import Any, Optional

from src.domain.candle import Candle


class InsideBarTrapStrategy:
    """Detect false breakouts of a mother-bar / inside-bar structure."""

    def detect_setup(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        if len(candles) < 3:
            return None

        mother_bar = candles[-3]
        inside_bar = candles[-2]
        trap_bar = candles[-1]

        if not self._is_inside_bar(mother_bar, inside_bar):
            return None

        mother_high = float(mother_bar.high)
        mother_low = float(mother_bar.low)
        trap_high = float(trap_bar.high)
        trap_low = float(trap_bar.low)
        trap_close = float(trap_bar.close)

        closes_back_inside = mother_low < trap_close < mother_high
        if not closes_back_inside:
            return None

        if trap_low < mother_low:
            return self._build_setup(
                trade_direction="LONG",
                trigger="BEAR_TRAP",
                trigger_candle=trap_bar,
                entry_price=trap_high,
                sl_price=trap_low,
            )

        if trap_high > mother_high:
            return self._build_setup(
                trade_direction="SHORT",
                trigger="BULL_TRAP",
                trigger_candle=trap_bar,
                entry_price=trap_low,
                sl_price=trap_high,
            )

        return None

    @staticmethod
    def _is_inside_bar(mother_bar: Candle, inside_bar: Candle) -> bool:
        return (
            float(inside_bar.high) <= float(mother_bar.high)
            and float(inside_bar.low) >= float(mother_bar.low)
        )

    @staticmethod
    def _build_setup(
        trade_direction: str,
        trigger: str,
        trigger_candle: Candle,
        entry_price: float,
        sl_price: float,
    ) -> dict[str, Any]:
        return {
            "symbol": trigger_candle.symbol,
            "timeframe": trigger_candle.timeframe,
            "strategy": "INSIDE_BAR_TRAP",
            "trade_direction": trade_direction,
            "trigger": trigger,
            "entry_price": round(float(entry_price), 2),
            "sl_price": round(float(sl_price), 2),
            "timestamp": int(trigger_candle.timestamp),
        }
