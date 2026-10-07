"""Market mood: trending, ranging or wild.

- wild:     recent bars are much bigger than usual (short ATR >= 1.8x long ATR)
- trending: price moved mostly in one direction (efficiency ratio >= 0.5)
- ranging:  everything else

MOOD_FILTER=block skips new ideas while the mood is wild. It is OFF by default
and the monthly history proof tests it ("mood_block" variant); switch it on
only when that proof suggests it.
"""

from __future__ import annotations

from typing import Sequence

WILD_RATIO = 1.8
TREND_EFFICIENCY = 0.5


def _true_ranges(candles: Sequence) -> list[float]:
    ranges = []
    for index in range(1, len(candles)):
        prev_close = float(candles[index - 1].close)
        high, low = float(candles[index].high), float(candles[index].low)
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return ranges


def mood(candles: Sequence, short: int = 14, long: int = 100, lookback: int = 20) -> str:
    if len(candles) < short + 2:
        return "unknown"
    ranges = _true_ranges(candles)
    short_atr = sum(ranges[-short:]) / short
    window = ranges[-long:]
    long_atr = sum(window) / len(window)
    if long_atr > 0 and short_atr / long_atr >= WILD_RATIO:
        return "wild"
    closes = [float(c.close) for c in candles[-(lookback + 1):]]
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path > 0 and abs(closes[-1] - closes[0]) / path >= TREND_EFFICIENCY:
        return "trending"
    return "ranging"


def mood_block_reason(candles: Sequence) -> str | None:
    current = mood(candles)
    if current == "wild":
        return "Market mood: wild (bars much bigger than usual)"
    return None
