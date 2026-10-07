"""Pass 2: slices of real trades, and rejected ideas the filters threw away."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.history.report import reason_category  # noqa: E402
from scripts.research.quality import DATA, MARKETS, load, split, stats  # noqa: E402
from src.analysis.evidence import cost_in_r  # noqa: E402

MIN_FIRST, MIN_LAST, MIN_AVG = 30, 12, 0.05


def session(hour: int) -> str:
    return "asia" if hour < 7 else "london" if hour < 12 else "newyork" if hour < 17 else "late"


def score_band(score: int) -> str:
    return "score<70" if score < 70 else "score70-84" if score < 85 else "score85+"


def robust(groups: dict, value: str = "r_actual") -> list[str]:
    lines = []
    for key, items in sorted(groups.items()):
        a, b = split(items)
        sa, sb = stats([r[value] for r in a]), stats([r[value] for r in b])
        if sa["n"] >= MIN_FIRST and sb["n"] >= MIN_LAST and sa["avg"] >= MIN_AVG and sb["avg"] >= MIN_AVG:
            lines.append(f"{' | '.join(map(str, key))} || FIRST {sa['n']} {sa['win']:.0f}% {sa['avg']:+.3f} "
                         f"|| LAST {sb['n']} {sb['win']:.0f}% {sb['avg']:+.3f}")
    return lines


def slices(rows: list[dict]) -> None:
    keys = {
        "market": lambda r: (r["market"],),
        "strategy (all markets)": lambda r: (r["strategy"],),
        "market+strategy": lambda r: (r["market"], r["strategy"]),
        "market+direction": lambda r: (r["market"], r["direction"]),
        "market+strategy+direction": lambda r: (r["market"], r["strategy"], r["direction"]),
        "market+session": lambda r: (r["market"], session(r["hour"])),
        "market+strategy+session": lambda r: (r["market"], r["strategy"], session(r["hour"])),
        "market+score": lambda r: (r["market"], score_band(r["score"])),
        "market+strategy+score": lambda r: (r["market"], r["strategy"], score_band(r["score"])),
        "market+order": lambda r: (r["market"], r["order"]),
    }
    for label, key in keys.items():
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        found = robust(groups)
        print(f"\n## {label}: {len(found)} robust of {len(groups)}")
        for line in found:
            print("  ", line)


def ideas() -> None:
    rows = []
    for market in MARKETS:
        path = DATA / market / "trades.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        symbol = payload["symbol"]
        for idea in payload.get("ideas", []):
            if idea.get("shadow_r") is None or str(idea.get("classification")) not in {"REJECTED", "BLOCKED"}:
                continue
            moment = datetime.fromtimestamp(int(idea["timestamp"]), tz=timezone.utc)
            cost = cost_in_r(symbol, idea.get("entry"), idea.get("sl"), bool(payload.get("cushion")))
            rows.append({
                "market": market,
                "strategy": str(idea.get("strategy") or "").upper(),
                "reason": reason_category(str(idea.get("classification")), str(idea.get("vetoes") or "")),
                "month": moment.strftime("%Y-%m"),
                "r_actual": float(idea["shadow_r"]) - cost,
            })
    print(f"\n\n# Rejected/blocked ideas with a known outcome: {len(rows)}")
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["market"], r["strategy"], r["reason"])].append(r)
    found = robust(groups)
    print(f"## filters that threw away money-makers (market | strategy | reason): {len(found)}")
    for line in found:
        print("  ", line)
    by_reason: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_reason[(r["market"], r["reason"])].append(r)
    found = robust(by_reason)
    print(f"## same, any strategy (market | reason): {len(found)}")
    for line in found:
        print("  ", line)


if __name__ == "__main__":
    trades, _ = load()
    slices(trades)
    ideas()
