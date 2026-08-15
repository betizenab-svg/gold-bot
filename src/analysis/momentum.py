from __future__ import annotations

from typing import Any, List, Optional

from src.domain.candle import Candle


def calculate_ema(values: List[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None

    multiplier = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
    return float(ema)


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Wilder-smoothed RSI."""
    if period <= 0 or len(closes) < period + 1:
        return None

    gains: List[float] = []
    losses: List[float] = []
    for index in range(1, len(closes)):
        delta = float(closes[index]) - float(closes[index - 1])
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[index]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[index]) / period

    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


class MomentumEngine:
    """RSI exhaustion veto plus EMA trend-alignment scoring.

    The exhaustion veto is the primary defense against chasing a move that
    already happened (late entries).
    """

    RSI_PERIOD = 14
    FAST_EMA = 21
    SLOW_EMA = 55
    OVERBOUGHT = 72.0
    OVERSOLD = 28.0

    def evaluate(self, candles: List[Candle], trade_direction: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "rsi": None,
            "score": 0,
            "veto": False,
            "notes": [],
        }
        if not isinstance(candles, list) or not candles:
            result["notes"].append("Momentum: insufficient history (neutral)")
            return result

        closes = [float(candle.close) for candle in candles]
        direction = str(trade_direction).upper()
        score = 0
        notes: List[str] = []

        rsi = calculate_rsi(closes, self.RSI_PERIOD)
        if rsi is not None:
            result["rsi"] = round(rsi, 1)
            if direction == "LONG" and rsi >= self.OVERBOUGHT:
                result["veto"] = True
                notes.append(f"RSI {rsi:.0f} overbought: long entry would chase exhaustion")
            elif direction == "SHORT" and rsi <= self.OVERSOLD:
                result["veto"] = True
                notes.append(f"RSI {rsi:.0f} oversold: short entry would chase exhaustion")
            elif direction == "LONG" and 45.0 <= rsi <= 65.0:
                score += 5
                notes.append(f"RSI {rsi:.0f}: healthy long momentum with room to run (+5)")
            elif direction == "SHORT" and 35.0 <= rsi <= 55.0:
                score += 5
                notes.append(f"RSI {rsi:.0f}: healthy short momentum with room to run (+5)")
            elif direction == "LONG" and rsi < 40.0:
                score -= 5
                notes.append(f"RSI {rsi:.0f}: weak momentum for a long (-5)")
            elif direction == "SHORT" and rsi > 60.0:
                score -= 5
                notes.append(f"RSI {rsi:.0f}: weak momentum for a short (-5)")

        fast_ema = calculate_ema(closes, self.FAST_EMA)
        slow_ema = calculate_ema(closes, self.SLOW_EMA)
        if fast_ema is not None and slow_ema is not None:
            last_close = closes[-1]
            if direction == "LONG" and last_close > fast_ema and fast_ema > slow_ema:
                score += 10
                notes.append("Price above EMA21 above EMA55: trend supports long (+10)")
            elif direction == "SHORT" and last_close < fast_ema and fast_ema < slow_ema:
                score += 10
                notes.append("Price below EMA21 below EMA55: trend supports short (+10)")

        if not notes:
            notes.append("Momentum: neutral")

        result["score"] = int(score)
        result["notes"] = notes
        return result
