from __future__ import annotations

from typing import Any, Optional

from config.settings import VALUE_AREA_SMA
from src.domain.candle import Candle


class BigBullsBearsStrategy:
    """Trend-following retracement strategy using SMA value areas and engulfing triggers."""

    TREND_PERIOD = 200

    def __init__(self, trend_period: int = 200, value_period: int = VALUE_AREA_SMA) -> None:
        self.trend_period = int(trend_period)
        self.value_period = int(value_period)

    def detect_setup(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        if len(candles) < self.trend_period:
            return None

        latest_index = len(candles) - 1
        sma_200 = self._calculate_sma(candles, latest_index, self.trend_period)
        if sma_200 is None:
            return None

        latest_close = float(candles[latest_index].close)
        if latest_close > sma_200:
            return self._detect_long_setup(candles)
        if latest_close < sma_200:
            return self._detect_short_setup(candles)
        return None

    def evaluate(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        return self.detect_setup(candles)

    def _detect_long_setup(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        for engulfing_index in (len(candles) - 1, len(candles) - 2):
            if engulfing_index < 1:
                continue

            previous_candle = candles[engulfing_index - 1]
            engulfing_candle = candles[engulfing_index]
            if not self._is_bullish_engulfing(previous_candle, engulfing_candle):
                continue

            touch_sma = self._find_touch_sma(candles, engulfing_index)
            if touch_sma is None:
                continue

            return self._build_setup(
                trade_direction="LONG",
                trigger="BULLISH_ENGULFING",
                trigger_candle=engulfing_candle,
                value_sma=touch_sma,
            )
        return None

    def _detect_short_setup(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        for engulfing_index in (len(candles) - 1, len(candles) - 2):
            if engulfing_index < 1:
                continue

            previous_candle = candles[engulfing_index - 1]
            engulfing_candle = candles[engulfing_index]
            if not self._is_bearish_engulfing(previous_candle, engulfing_candle):
                continue

            touch_sma = self._find_touch_sma(candles, engulfing_index)
            if touch_sma is None:
                continue

            return self._build_setup(
                trade_direction="SHORT",
                trigger="BEARISH_ENGULFING",
                trigger_candle=engulfing_candle,
                value_sma=touch_sma,
            )
        return None

    def _build_setup(
        self,
        trade_direction: str,
        trigger: str,
        trigger_candle: Candle,
        value_sma: float,
    ) -> dict[str, Any]:
        if trade_direction == "LONG":
            stop_loss = float(trigger_candle.low)
        else:
            stop_loss = float(trigger_candle.high)

        return {
            "symbol": trigger_candle.symbol,
            "timeframe": trigger_candle.timeframe,
            "trade_direction": trade_direction,
            "entry_price": round(float(trigger_candle.close), 2),
            "stop_loss": round(stop_loss, 2),
            "strategy": "BIG_BULLS_BEARS",
            "trigger": trigger,
            "value_area_sma": round(value_sma, 2),
            "timestamp": int(trigger_candle.timestamp),
        }

    def _find_touch_sma(self, candles: list[Candle], engulfing_index: int) -> Optional[float]:
        for candidate_index in (engulfing_index, engulfing_index - 1):
            sma = self._calculate_sma(candles, candidate_index, self.value_period)
            if sma is None:
                continue
            if self._touches_sma(candles[candidate_index], sma):
                return sma
        return None

    @staticmethod
    def _calculate_sma(
        candles: list[Candle],
        end_index: int,
        period: int,
    ) -> Optional[float]:
        start_index = end_index - period + 1
        if start_index < 0:
            return None

        window = candles[start_index : end_index + 1]
        closes = [float(candle.close) for candle in window]
        return sum(closes) / period

    @staticmethod
    def _touches_sma(candle: Candle, sma_value: float) -> bool:
        return float(candle.low) <= sma_value <= float(candle.high)

    @staticmethod
    def _is_bullish_engulfing(previous_candle: Candle, current_candle: Candle) -> bool:
        return (
            float(previous_candle.close) < float(previous_candle.open)
            and float(current_candle.close) > float(current_candle.open)
            and float(current_candle.open) <= float(previous_candle.close)
            and float(current_candle.close) >= float(previous_candle.open)
        )

    @staticmethod
    def _is_bearish_engulfing(previous_candle: Candle, current_candle: Candle) -> bool:
        return (
            float(previous_candle.close) > float(previous_candle.open)
            and float(current_candle.close) < float(current_candle.open)
            and float(current_candle.open) >= float(previous_candle.close)
            and float(current_candle.close) <= float(previous_candle.open)
        )
