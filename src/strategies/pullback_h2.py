from __future__ import annotations

from typing import Any, Optional

from config.instruments import get_instrument, scaled_buffer
from config.settings import ENTRY_BUFFER_PTS
from src.analysis.momentum import calculate_ema
from src.domain.candle import Candle


class PullbackH2L2Strategy:
    """Brooks High-2 / Low-2: enter the second leg of a with-trend pullback.

    Bull case: trend up (close above EMA21), price pulls back from a recent
    peak; the first bar whose high exceeds the prior bar's high is H1; after
    at least one lower-high bar, the next bar whose high exceeds the prior
    bar's high is H2 — BUY STOP above it. The signal fires only when the H2
    bar is the latest candle, so live pulses catch it exactly once.
    """

    LOOKBACK = 15
    MIN_PULLBACK_BARS = 3

    def __init__(self, entry_buffer_pts: float = ENTRY_BUFFER_PTS) -> None:
        self.entry_buffer_pts = float(entry_buffer_pts)

    def detect_setup(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        if len(candles) < 25:
            return None

        closes = [float(c.close) for c in candles]
        ema21 = calculate_ema(closes, 21)
        if ema21 is None:
            return None

        last_close = closes[-1]
        if last_close > ema21:
            return self._detect_bull_h2(candles)
        if last_close < ema21:
            return self._detect_bear_l2(candles)
        return None

    def _detect_bull_h2(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        window = candles[-self.LOOKBACK :]
        highs = [float(c.high) for c in window]
        peak_index = max(range(len(window)), key=lambda i: highs[i])
        pullback = window[peak_index + 1 :]
        if len(pullback) < self.MIN_PULLBACK_BARS:
            return None

        # Count "H" events: bars whose high exceeds the prior bar's high.
        h_events = []
        for index in range(1, len(pullback)):
            if float(pullback[index].high) > float(pullback[index - 1].high):
                h_events.append(index)

        if len(h_events) != 2 or h_events[-1] != len(pullback) - 1:
            return None
        # A lower-high bar must separate H1 and H2 (a real second leg).
        if h_events[1] - h_events[0] < 2:
            return None

        signal_bar = pullback[-1]
        # Signal-bar quality: trend bar, close in upper half.
        bar_range = float(signal_bar.high) - float(signal_bar.low)
        if bar_range <= 0:
            return None
        close_position = (float(signal_bar.close) - float(signal_bar.low)) / bar_range
        if close_position < 0.5:
            return None

        return self._build(signal_bar, "LONG", "H2_PULLBACK")

    def _detect_bear_l2(self, candles: list[Candle]) -> Optional[dict[str, Any]]:
        window = candles[-self.LOOKBACK :]
        lows = [float(c.low) for c in window]
        trough_index = min(range(len(window)), key=lambda i: lows[i])
        pullback = window[trough_index + 1 :]
        if len(pullback) < self.MIN_PULLBACK_BARS:
            return None

        l_events = []
        for index in range(1, len(pullback)):
            if float(pullback[index].low) < float(pullback[index - 1].low):
                l_events.append(index)

        if len(l_events) != 2 or l_events[-1] != len(pullback) - 1:
            return None
        if l_events[1] - l_events[0] < 2:
            return None

        signal_bar = pullback[-1]
        bar_range = float(signal_bar.high) - float(signal_bar.low)
        if bar_range <= 0:
            return None
        close_position = (float(signal_bar.close) - float(signal_bar.low)) / bar_range
        if close_position > 0.5:
            return None

        return self._build(signal_bar, "SHORT", "L2_PULLBACK")

    def _build(self, signal_bar: Candle, direction: str, strategy: str) -> dict[str, Any]:
        buffer = scaled_buffer(signal_bar.symbol, self.entry_buffer_pts)
        if direction == "LONG":
            entry = float(signal_bar.high) + buffer
            sl = float(signal_bar.low) - buffer
        else:
            entry = float(signal_bar.low) - buffer
            sl = float(signal_bar.high) + buffer

        nd = get_instrument(signal_bar.symbol).price_decimals
        return {
            "symbol": signal_bar.symbol,
            "timeframe": signal_bar.timeframe,
            "strategy": strategy,
            "trade_direction": direction,
            "order_type": "STOP",
            "entry_price": round(entry, nd),
            "sl_price": round(sl, nd),
            "timestamp": int(signal_bar.timestamp),
        }
