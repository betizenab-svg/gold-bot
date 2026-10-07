"""Combine the surviving gold strategies into one system and test it on the
Alpha Pro 8% challenge (Phase 1 +8%, Phase 2 +5%, max loss 8%, daily 4%).
"""

from __future__ import annotations

import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.research import gold_lab as lab  # noqa: E402
from scripts.research.robust import YEARS, run_all  # noqa: E402
from scripts.research.robust2 import weekend_safe  # noqa: E402

COMPONENT_SETS = {
    "breakout 4h": ("breakout4h",),
    "4h trio": ("breakout4h", "squeeze4h", "pullback4h"),
    "4h trio + breakout 1h": ("breakout4h", "squeeze4h", "pullback4h", "breakout1h"),
}
COMPONENTS = {
    "breakout4h": lambda: lab.donchian_break("4h", 30, 1.5, 1.0, 30, "none"),
    "squeeze4h": lambda: lab.squeeze_break("4h", 60, 1.5, 1.0, 30, "none"),
    "pullback4h": lambda: lab.ema_pullback("4h", 2.0, 1.0, 24),
    "breakout1h": lambda: lab.donchian_break("1h", 20, 2.0, 0.75, 30, "none"),
}
MAX_SAME_DIRECTION = 2


def trades(names: tuple[str, ...]) -> list[dict]:
    candidates = []
    for name in names:
        for res in run_all(lab.gold(), weekend_safe(COMPONENTS[name](), False)):
            res["component"] = name
            candidates.append(res)
    taken: list[dict] = []
    for res in sorted(candidates, key=lambda x: x["time"]):
        open_same = [t for t in taken if t["exit"] > res["time"] and t["direction"] == res["direction"]]
        open_same_component = [t for t in open_same if t["component"] == res["component"]]
        if len(open_same) >= MAX_SAME_DIRECTION or open_same_component:
            continue
        taken.append(res)
    return taken


def yearly(rows: list[dict]) -> str:
    parts = []
    for name, a, b in YEARS:
        sel = [x["r"] for x in rows if pd.Timestamp(a, tz="UTC") <= x["time"] < pd.Timestamp(b, tz="UTC")]
        if sel:
            parts.append(f"{name}: {len(sel)} trades {100 * np.mean(np.array(sel) > 0):.0f}% win {sum(sel):+.1f}R")
    return " | ".join(parts)


def challenge(rows: list[dict], risk: float, runs: int = 4000) -> tuple[float, float]:
    days = defaultdict(list)
    for x in rows:
        days[x["time"].strftime("%Y-%m-%d")].append(x["r"])
    day_list = list(days.values())
    trading_days = len({x["time"].strftime("%Y-%m-%d") for x in rows})
    span_days = (rows[-1]["time"] - rows[0]["time"]).days or 1
    rng = random.Random(5)

    def phase(target: float) -> tuple[bool, int]:
        balance, used = 0.0, 0
        while True:
            start = balance
            for r in rng.choice(day_list):
                balance += risk * r
                used += 1
                if start - balance >= 4.0 or balance <= -8.0:
                    return False, used
                if balance >= target:
                    return True, used
            if used > 5000:
                return False, used

    passed, needed = 0, []
    for _ in range(runs):
        ok1, n1 = phase(8.0)
        if ok1:
            ok2, n2 = phase(5.0)
            if ok2:
                passed += 1
                needed.append(n1 + n2)
    per_week = len(rows) / (span_days / 7)
    weeks = statistics.median(needed) / per_week if needed else float("nan")
    del trading_days
    return 100.0 * passed / runs, weeks


if __name__ == "__main__":
    for label, names in COMPONENT_SETS.items():
        rows = trades(names)
        r = np.array([x["r"] for x in rows])
        print(f"\n=== {label}: {len(r)} trades ({len(r) / 3:.0f} a year), {100 * (r > 0).mean():.0f}% win, "
              f"{r.mean():+.3f}R avg, {r.sum():+.1f}R total")
        print("by year:", yearly(rows))
        for name in names:
            sub = [x for x in rows if x["component"] == name]
            print(f"  {name}: {yearly(sub)}")
        for risk in (0.5, 1.0, 1.5):
            chance, weeks = challenge(rows, risk)
            print(f"  risk {risk}%: passes both phases {chance:.0f}% of the time, typically within {weeks:.0f} weeks")
