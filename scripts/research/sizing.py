"""Same signals, smarter sizing: does lowering the risk after losses (and only
then) pass the Alpha challenge more often at the same speed?

Usage: python scripts/research/sizing.py
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Callable, Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research.portfolio import daily_pnl, portfolio, rsi2_trades, system_trades  # noqa: E402

RiskRule = Callable[[float], float]


def walk(rows: list[dict], rule: RiskRule) -> tuple[float, float, float]:
    """Like portfolio.challenge, but the risk per trade follows `rule(balance %)`."""
    _days, pnl = daily_pnl(rows)
    n = len(pnl)
    weeks: list[float] = []

    def phase(start: int, target: float) -> tuple[Optional[bool], int]:
        balance = 0.0
        for step in range(n):
            total, losses = pnl[(start + step) % n]
            risk = rule(balance)
            if risk * losses <= -4.0 or balance + risk * losses <= -8.0:
                return False, step + 1
            balance += risk * total
            if balance <= -8.0:
                return False, step + 1
            if balance >= target:
                return True, step + 1
        return None, n

    for start in range(n):
        ok1, used1 = phase(start, 8.0)
        if ok1:
            ok2, used2 = phase((start + used1) % n, 5.0)
            if ok2:
                weeks.append((used1 + used2) / 5.0)
    if not weeks:
        return 0.0, float("nan"), float("nan")
    return 100.0 * len(weeks) / n, statistics.median(weeks), float(np.percentile(weeks, 80))


def flat(risk: float) -> RiskRule:
    return lambda balance: risk


def ladder(top: float, middle: float, bottom: float, step1: float = -2.0, step2: float = -4.0) -> RiskRule:
    """`top` while at or above the start, `middle` below step1 %, `bottom` below step2 %."""
    return lambda balance: top if balance > step1 else middle if balance > step2 else bottom


RULES: dict[str, RiskRule] = {
    "flat 1.0%": flat(1.0),
    "flat 1.25%": flat(1.25),
    "flat 1.5%": flat(1.5),
    "1.25 / 1.0 below -2% / 0.5 below -4%": ladder(1.25, 1.0, 0.5),
    "1.5 / 1.0 below -2% / 0.5 below -4%": ladder(1.5, 1.0, 0.5),
    "1.5 / 0.75 below -2% / 0.5 below -4%": ladder(1.5, 0.75, 0.5),
    "2.0 / 1.0 below -2% / 0.5 below -4%": ladder(2.0, 1.0, 0.5),
    "1.5 / 1.0 below -3% / 0.5 below -5%": ladder(1.5, 1.0, 0.5, -3.0, -5.0),
}


def main() -> None:
    gold = system_trades("XAUUSD")
    us500 = rsi2_trades("US500", 10, 3.0, 0.75, 5)
    for label, rows in (("gold system", gold), ("gold + US500 RSI2", portfolio([gold, us500], max_open=4))):
        print(f"\n=== {label}: {len(rows)} trades")
        for name, rule in RULES.items():
            chance, median_weeks, slow_weeks = walk(rows, rule)
            print(f"   {name:42s} passes {chance:5.1f}% | median {median_weeks:4.0f} weeks | slowest 20%: {slow_weeks:4.0f}+",
                  flush=True)


if __name__ == "__main__":
    main()
