"""Does the one book rule that survived (Kennedy's moving-average channel on gold
4-hour charts) make the live gold system pass the challenge faster at the same
safety? Faithful replay with the bot's own code, sizing chosen on the first 24
months, then checked on the hidden 12.

Usage: python scripts/research/book_replay.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research import gold_v2_replay as v2  # noqa: E402
from src.strategies import gold_system as s  # noqa: E402

MA_CHANNEL = "GOLD_MA_CHANNEL"
s.PLANS[MA_CHANNEL] = s.Plan(1.5, 2.0, 30, 3)
s.TRIGGER_TEXT[MA_CHANNEL] = "ma channel"


def ma_channel(candles) -> int:
    """The 5-candle average of closes crosses above the 20-candle average of highs (buy) or below that of lows (sell)."""
    if len(candles) < 30:
        return 0
    closes = [float(c.close) for c in candles[-6:]]
    highs = [float(c.high) for c in candles[-21:]]
    lows = [float(c.low) for c in candles[-21:]]
    fast_now, fast_prev = sum(closes[1:]) / 5, sum(closes[:-1]) / 5
    top_now, top_prev = sum(highs[1:]) / 20, sum(highs[:-1]) / 20
    bottom_now, bottom_prev = sum(lows[1:]) / 20, sum(lows[:-1]) / 20
    if fast_now > top_now and fast_prev <= top_prev:
        return 1
    if fast_now < bottom_now and fast_prev >= bottom_prev:
        return -1
    return 0


v2.FUNCS[MA_CHANNEL] = ma_channel
v2.VARIANTS = {
    "v1 (live)": (v2.V1, 2),
    "book MA channel only": ([MA_CHANNEL], 2),
    "v1, then MA channel": (v2.V1 + [MA_CHANNEL], 2),
    "MA channel, then v1": ([MA_CHANNEL] + v2.V1, 2),
    "v1, then MA channel, cap 3": (v2.V1 + [MA_CHANNEL], 3),
}

if __name__ == "__main__":
    v2.main()
