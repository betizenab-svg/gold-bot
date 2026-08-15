from __future__ import annotations

from typing import Any, List, Optional

from config.settings import ENTRY_BUFFER_PTS, TIMEFRAME_SECONDS
from src.domain.candle import Candle


class QuasimodoStrategy:
    """RTM Quasimodo: a sweep of the prior swing (the 'head') followed by a
    break of the intervening neckline arms a LIMIT order back at the left
    shoulder — entering where the trapped side must exit.

    Bearish: highs ... SH1 (left shoulder), then SH2 > SH1 (head/sweep), an
    intervening low L1 between them, and the current close below L1 -> SELL
    LIMIT at SH1, stop above SH2.
    """

    MAX_AGE_BARS = 40

    def __init__(self, entry_buffer_pts: float = ENTRY_BUFFER_PTS) -> None:
        self.entry_buffer_pts = float(entry_buffer_pts)

    def detect_setup(
        self,
        candles: List[Candle],
        swing_history: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if not isinstance(candles, list) or len(candles) < 5:
            return None
        if not isinstance(swing_history, dict):
            return None

        highs = self._pivots(swing_history.get("highs"))
        lows = self._pivots(swing_history.get("lows"))
        current = candles[-1]
        step = int(TIMEFRAME_SECONDS.get(current.timeframe, 60))
        max_age_seconds = self.MAX_AGE_BARS * step

        bearish = self._detect_bearish(highs, lows, current, max_age_seconds)
        if bearish is not None:
            return bearish
        return self._detect_bullish(highs, lows, current, max_age_seconds)

    @staticmethod
    def _pivots(raw: Any) -> List[dict[str, float]]:
        if not isinstance(raw, list):
            return []
        output: List[dict[str, float]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                output.append(
                    {"timestamp": int(item["timestamp"]), "price": float(item["price"])}
                )
            except (KeyError, TypeError, ValueError):
                continue
        return output

    def _detect_bearish(
        self,
        highs: List[dict[str, float]],
        lows: List[dict[str, float]],
        current: Candle,
        max_age_seconds: int,
    ) -> Optional[dict[str, Any]]:
        if len(highs) < 2:
            return None
        sh1, sh2 = highs[-2], highs[-1]
        if sh2["price"] <= sh1["price"]:
            return None
        if int(current.timestamp) - int(sh2["timestamp"]) > max_age_seconds:
            return None

        neckline = self._pivot_between(lows, sh1["timestamp"], sh2["timestamp"])
        if neckline is None:
            return None

        close = float(current.close)
        # Neckline broken, but price hasn't already returned past the shoulder.
        if close >= neckline["price"] or close >= sh1["price"]:
            return None

        entry = round(float(sh1["price"]), 2)
        sl = round(float(sh2["price"]) + self.entry_buffer_pts, 2)
        return {
            "symbol": current.symbol,
            "timeframe": current.timeframe,
            "strategy": "QUASIMODO",
            "trade_direction": "SHORT",
            "order_type": "LIMIT",
            "entry_price": entry,
            "sl_price": sl,
            "timestamp": int(current.timestamp),
        }

    def _detect_bullish(
        self,
        highs: List[dict[str, float]],
        lows: List[dict[str, float]],
        current: Candle,
        max_age_seconds: int,
    ) -> Optional[dict[str, Any]]:
        if len(lows) < 2:
            return None
        sl1, sl2 = lows[-2], lows[-1]
        if sl2["price"] >= sl1["price"]:
            return None
        if int(current.timestamp) - int(sl2["timestamp"]) > max_age_seconds:
            return None

        neckline = self._pivot_between(highs, sl1["timestamp"], sl2["timestamp"])
        if neckline is None:
            return None

        close = float(current.close)
        if close <= neckline["price"] or close <= sl1["price"]:
            return None

        entry = round(float(sl1["price"]), 2)
        stop = round(float(sl2["price"]) - self.entry_buffer_pts, 2)
        return {
            "symbol": current.symbol,
            "timeframe": current.timeframe,
            "strategy": "QUASIMODO",
            "trade_direction": "LONG",
            "order_type": "LIMIT",
            "entry_price": entry,
            "sl_price": stop,
            "timestamp": int(current.timestamp),
        }

    @staticmethod
    def _pivot_between(
        pivots: List[dict[str, float]],
        start_ts: float,
        end_ts: float,
    ) -> Optional[dict[str, float]]:
        candidates = [
            p for p in pivots if int(start_ts) < int(p["timestamp"]) < int(end_ts)
        ]
        return candidates[-1] if candidates else None
