"""Luck test: how bad could an honest strategy's run of results get?

The same trade results are reshuffled thousands of times; the spread of the
worst dips and losing streaks shows what to expect from bad luck alone.
"""

from __future__ import annotations

import random
from typing import Sequence


def max_drawdown(r_values: Sequence[float]) -> float:
    peak = 0.0
    total = 0.0
    worst = 0.0
    for value in r_values:
        total += value
        peak = max(peak, total)
        worst = min(worst, total - peak)
    return round(abs(worst), 4)


def longest_losing_streak(r_values: Sequence[float]) -> int:
    longest = current = 0
    for value in r_values:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(share * (len(ordered) - 1)))))
    return ordered[index]


def luck_test(r_values: Sequence[float], runs: int = 5000, seed: int = 7) -> dict[str, float]:
    """Typical (median) and bad-luck (95th percentile) dip and streak."""
    values = [float(v) for v in r_values]
    if len(values) < 5:
        return {"trades": len(values)}
    rng = random.Random(seed)
    dips: list[float] = []
    streaks: list[float] = []
    for _ in range(int(runs)):
        shuffled = values[:]
        rng.shuffle(shuffled)
        dips.append(max_drawdown(shuffled))
        streaks.append(float(longest_losing_streak(shuffled)))
    return {
        "trades": len(values),
        "total_r": round(sum(values), 2),
        "actual_max_drawdown_r": max_drawdown(values),
        "actual_losing_streak": longest_losing_streak(values),
        "typical_max_drawdown_r": round(_percentile(dips, 0.5), 2),
        "bad_luck_max_drawdown_r": round(_percentile(dips, 0.95), 2),
        "typical_losing_streak": int(_percentile(streaks, 0.5)),
        "bad_luck_losing_streak": int(_percentile(streaks, 0.95)),
    }
