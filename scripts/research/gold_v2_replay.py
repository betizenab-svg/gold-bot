"""Faithful replay (the bot's own gold_system code: one signal per candle, quiet
periods, same-direction cap, weekend rule, 5-minute resolution) of trigger sets
for Gold System v2, chosen on the first 24 months and checked on the hidden 12.

Usage: python scripts/research/gold_v2_replay.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research import gold_system_replay as replay_mod  # noqa: E402
from scripts.research.engine import YEAR1, YEAR2, stats  # noqa: E402
from scripts.research.sizing import ladder, walk  # noqa: E402
from src.strategies import gold_system as s  # noqa: E402

# Trend-filtered triggers from scripts/research/discover.py: only with the trend
# (close beyond the 200 average and the 50 average beyond the 200).
TREND_BREAKOUT, MOMENTUM, COIL, INSIDE = "GOLD_TREND_BREAKOUT", "GOLD_MOMENTUM", "GOLD_COIL", "GOLD_INSIDE"
s.PLANS.update({
    TREND_BREAKOUT: s.Plan(1.5, 2.0, 30, 3), MOMENTUM: s.Plan(1.5, 2.0, 30, 3),
    COIL: s.Plan(2.0, 3.0, 30, 3), INSIDE: s.Plan(1.0, 1.0, 30, 3),
})
s.TRIGGER_TEXT.update({name: name.lower() for name in (TREND_BREAKOUT, MOMENTUM, COIL, INSIDE)})
WARMUP = 250


def _trend(closes) -> int:
    e50, e200 = s._ema(closes, 50), s._ema(closes, 200)
    if closes[-1] > e200 and e50 > e200:
        return 1
    if closes[-1] < e200 and e50 < e200:
        return -1
    return 0


def trend_breakout(candles, n: int = 20) -> int:
    if len(candles) < WARMUP:
        return 0
    side = _trend([float(c.close) for c in candles])
    previous, close = candles[-n - 1:-1], float(candles[-1].close)
    if side > 0 and close > max(float(c.high) for c in previous):
        return 1
    if side < 0 and close < min(float(c.low) for c in previous):
        return -1
    return 0


def momentum(candles, k: float = 1.5) -> int:
    """The first close beyond the 20 average +/- k ATR (Keltner channel), with the trend."""
    if len(candles) < WARMUP:
        return 0
    closes = [float(c.close) for c in candles]
    side = _trend(closes)
    now_mid, now_atr = s._ema(closes, 20), s._atr(candles)
    prev_mid, prev_atr = s._ema(closes[:-1], 20), s._atr(candles[:-1])
    if side > 0 and closes[-1] > now_mid + k * now_atr and not closes[-2] > prev_mid + k * prev_atr:
        return 1
    if side < 0 and closes[-1] < now_mid - k * now_atr and not closes[-2] < prev_mid - k * prev_atr:
        return -1
    return 0


def coil(candles, n: int = 4) -> int:
    """The previous candle was the narrowest of the last n; this close breaks it, with the trend."""
    if len(candles) < WARMUP:
        return 0
    side = _trend([float(c.close) for c in candles])
    ranges = [float(c.high) - float(c.low) for c in candles[-n - 1:-1]]
    previous, close = candles[-2], float(candles[-1].close)
    if ranges[-1] != min(ranges):
        return 0
    if side > 0 and close > float(previous.high):
        return 1
    if side < 0 and close < float(previous.low):
        return -1
    return 0


def inside(candles) -> int:
    """The previous candle sat inside the one before; this close breaks that mother candle, with the trend."""
    if len(candles) < WARMUP:
        return 0
    side = _trend([float(c.close) for c in candles])
    mother, baby, close = candles[-3], candles[-2], float(candles[-1].close)
    if not (float(baby.high) < float(mother.high) and float(baby.low) > float(mother.low)):
        return 0
    if side > 0 and close > float(mother.high):
        return 1
    if side < 0 and close < float(mother.low):
        return -1
    return 0


FUNCS = {
    s.BREAKOUT: s.breakout, s.SQUEEZE: s.squeeze, s.PULLBACK: s.pullback,
    TREND_BREAKOUT: trend_breakout, MOMENTUM: momentum, COIL: coil, INSIDE: inside,
}
V1 = [s.BREAKOUT, s.SQUEEZE, s.PULLBACK]
V2 = [MOMENTUM, COIL, TREND_BREAKOUT, INSIDE]
VARIANTS = {
    "v1 (live)": (V1, 2),
    "v2 only": (V2, 2),
    "v2 first, then v1": (V2 + V1, 2),
    "v1 first, then v2": (V1[:2] + V2 + V1[2:], 2),
    "v2 first, then v1, cap 3": (V2 + V1, 3),
}


def run(order: list[str], cap: int) -> list[dict]:
    s.TRIGGERS = tuple((name, FUNCS[name]) for name in order)
    s.MAX_SAME_DIRECTION = cap
    return replay_mod.replay()


def describe(rows: list[dict]) -> str:
    t = np.array([x["time"].timestamp() for x in rows])
    r = np.array([x["r"] for x in rows])
    parts = []
    for label, sel in (("Y1", t < YEAR1), ("Y2", (t >= YEAR1) & (t < YEAR2)), ("hidden Y3", t >= YEAR2)):
        st = stats(r[sel])
        parts.append(f"{label} {st['n']} tr {st['avg']:+.2f}R {st['total']:+.1f}R")
    st = stats(r)
    return (f"{st['n']} trades ({st['n'] / 3:.0f}/yr) {st['win']:.0%} win {st['avg']:+.3f}R avg {st['total']:+.1f}R "
            f"t={st['t']:.1f} | " + " | ".join(parts))


def main() -> None:
    for label, (order, cap) in VARIANTS.items():
        rows = run(order, cap)
        disc = [x for x in rows if x["time"].timestamp() < YEAR2]
        hidden = [x for x in rows if x["time"].timestamp() >= YEAR2]
        print(f"\n=== {label}: {' > '.join(name.replace('GOLD_', '') for name in order)}, cap {cap}\n   {describe(rows)}")
        for name in order:
            sub = [x for x in rows if x["component"] == name]
            if sub:
                print(f"      {name}: {describe(sub)}")
        best = None
        for scale in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4):
            chance, weeks, _ = walk(disc, ladder(1.5 * scale, 1.0 * scale, 0.5 * scale))
            if chance >= 97.0:
                best = (scale, weeks)
        if best is None:
            print("   no ladder size passes 97% of start days in the first 24 months", flush=True)
            continue
        scale, weeks = best
        rule = ladder(1.5 * scale, 1.0 * scale, 0.5 * scale)
        h_chance, h_weeks, _ = walk(hidden, rule)
        a_chance, a_weeks, a_slow = walk(rows, rule)
        print(f"   safe ladder: top {1.5 * scale:.2f}% / {1.0 * scale:.2f}% / {0.5 * scale:.2f}% -> first 24m median "
              f"{weeks:.0f} wk | hidden 12m {h_chance:.0f}% pass, {h_weeks:.0f} wk | all 36m {a_chance:.0f}% pass, "
              f"{a_weeks:.0f} wk (slowest 20% {a_slow:.0f}+)", flush=True)


if __name__ == "__main__":
    main()
