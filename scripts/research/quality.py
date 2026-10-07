"""Quality research on the cloud 3-year proofs (non-crypto markets).

Every number is after spread and slippage. "First 24 months" finds patterns;
"last 12 months" (never used for choosing) proves them.
Single-target exits at X R are exact for X <= 1.0: excursions are recorded
only on candles before the exit candle, so this is, if anything, pessimistic.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.history.report import closed_trades  # noqa: E402

DATA = ROOT / "data" / "research" / "cloud"
MARKETS = ["XAUUSD", "XAUUSD_H1", "XAUUSD_H4", "XAGUSD", "EURUSD", "EURUSD_H1", "GBPUSD",
           "GBPUSD_H1", "USDJPY", "AUDUSD", "US100", "US500", "WTIUSD"]
TARGETS = (0.3, 0.5, 0.75, 1.0)


def load() -> tuple[list[dict], dict]:
    rows: list[dict] = []
    windows = {}
    for market in MARKETS:
        path = DATA / market / "trades.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        windows[market] = payload.get("window")
        raw = {str(t.get("id")): t for t in payload.get("trades", [])}
        for trade in closed_trades(payload):
            source = raw.get(str(trade.get("id"))) or {}
            moment = datetime.fromtimestamp(int(source.get("timestamp") or trade.get("timestamp") or 0), tz=timezone.utc)
            cost = trade["r_raw"] - trade["r"]
            mfe = float(source.get("mfe_r") or 0.0)
            mae = float(source.get("mae_r") or 0.0)
            row = {
                "market": market,
                "strategy": trade["strategy"],
                "direction": str(source.get("direction") or ""),
                "score": int(source.get("score") or 0),
                "order": str(source.get("order_type") or ""),
                "hour": moment.hour,
                "weekday": moment.weekday(),
                "month": moment.strftime("%Y-%m"),
                "ts": int(moment.timestamp()),
                "cost": cost,
                "mfe": mfe,
                "mae": mae,
                "r_actual": trade["r"],
                "r_raw": trade["r_raw"],
            }
            for x in TARGETS:
                row[f"r_{x}"] = (x if mfe >= x else min(trade["r_raw"], x)) - cost
            rows.append(row)
    return rows, windows


def stats(values: list[float]) -> dict:
    n = len(values)
    if not n:
        return {"n": 0, "win": 0.0, "avg": 0.0, "total": 0.0}
    wins = sum(1 for v in values if v > 0)
    return {"n": n, "win": 100.0 * wins / n, "avg": sum(values) / n, "total": sum(values)}


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    months = sorted({r["month"] for r in rows})
    cut = months[-12] if len(months) >= 13 else months[-1]
    return [r for r in rows if r["month"] < cut], [r for r in rows if r["month"] >= cut]


if __name__ == "__main__":
    rows, windows = load()
    print("windows:", {k: v for k, v in list(windows.items())[:2]}, "...")
    first, last = split(rows)
    print(f"{len(rows)} trades; first part {len(first)}, last 12 months {len(last)}\n")
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["market"], r["strategy"])].append(r)
    exits = ["r_actual"] + [f"r_{x}" for x in TARGETS]
    print("market | strategy | exit | FIRST n win% avg | LAST n win% avg")
    for (market, strategy), items in sorted(groups.items()):
        a, b = split(items)
        for exit_name in exits:
            sa, sb = stats([r[exit_name] for r in a]), stats([r[exit_name] for r in b])
            if sa["n"] >= 20 and sa["avg"] > 0 and sb["n"] >= 10 and sb["avg"] > 0:
                print(f"{market} | {strategy} | {exit_name} | {sa['n']} {sa['win']:.0f}% {sa['avg']:+.3f} | "
                      f"{sb['n']} {sb['win']:.0f}% {sb['avg']:+.3f}")
