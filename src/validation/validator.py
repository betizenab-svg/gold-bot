from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from config.settings import TIMEFRAME_SECONDS
from src.domain.candle import Candle


class DataValidator:
    def validate_candle(self, candle: Candle) -> bool:
        if candle.volume == 0:
            return False
        if candle.high < candle.low:
            return False
        if candle.open <= 0 or candle.high <= 0 or candle.low <= 0 or candle.close <= 0:
            return False
        timestamp = int(candle.timestamp)
        weekday = datetime.fromtimestamp(timestamp, timezone.utc).weekday()
        if weekday >= 5:
            return False
        return True

    def filter_candles(self, candles: Iterable[Candle]) -> List[Candle]:
        return [candle for candle in candles if self.validate_candle(candle)]

    def detect_gaps(self, sorted_candles: Iterable[Candle], timeframe: str) -> List[int]:
        candles = list(sorted_candles)
        if len(candles) < 2:
            return []
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        expected_diff = TIMEFRAME_SECONDS[timeframe]
        missing: List[int] = []
        prev_ts = int(candles[0].timestamp)
        for candle in candles[1:]:
            current_ts = int(candle.timestamp)
            gap = current_ts - prev_ts
            if gap > expected_diff:
                next_ts = prev_ts + expected_diff
                while next_ts < current_ts:
                    missing.append(next_ts)
                    next_ts += expected_diff
            prev_ts = current_ts
        return missing
