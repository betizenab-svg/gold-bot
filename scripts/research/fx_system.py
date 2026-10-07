"""A second engine for the challenge: my volatility-contraction breakout
(families.contraction_breakout) on every FX pair with the SAME settings,
added to the live gold system. Chosen on the first 24 months, checked on the
hidden 12, with the bot's portfolio limits (open trades, same dollar bet).

Usage: python scripts/research/fx_system.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config.instruments import INSTRUMENTS  # noqa: E402
from scripts.research import gold_system_replay as gold_replay  # noqa: E402
from scripts.research.engine import YEAR1, YEAR2, bars, market, simulate, stats  # noqa: E402
from scripts.research.families import Ind, contraction_breakout  # noqa: E402
from scripts.research.sizing import ladder, walk  # noqa: E402

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "AUDJPY", "EURGBP", "GBPJPY"]
PLANS = {"1R": (1.5, 1.0, 30), "2R": (1.5, 2.0, 30)}


def fx_trades(pair: str, plan: str) -> list[dict]:
    stop_atr, target_r, hold = PLANS[plan]
    data = market(pair)
    f = bars(data, "H4")
    x = Ind(f)
    rows = simulate(data, f, contraction_breakout(x, 0.7, 5), stop_atr * x.atr(14).to_numpy(), target_r, hold)
    usd = INSTRUMENTS[pair].usd_exposure
    return [
        {"time": pd.Timestamp(int(t0), unit="s", tz="UTC"), "exit": pd.Timestamp(int(t1), unit="s", tz="UTC"),
         "direction": int(d), "r": float(r), "market": pair, "usd": usd * int(d)}
        for t0, t1, d, r in rows
    ]


def gold_trades() -> list[dict]:
    rows = gold_replay.replay()
    for x in rows:
        x.update(market="XAUUSD", usd=-int(x["direction"]))
    return rows


def combine(streams: list[list[dict]], max_open: int, max_same_usd: int) -> list[dict]:
    merged = sorted((x for s in streams for x in s), key=lambda x: x["time"])
    taken: list[dict] = []
    for x in merged:
        live = [t for t in taken if t["exit"] > x["time"]]
        if len(live) >= max_open:
            continue
        if x["usd"] and sum(1 for t in live if t["usd"] == x["usd"]) >= max_same_usd:
            continue
        taken.append(x)
    return taken


def period_line(rows: list[dict]) -> str:
    t = np.array([x["time"].timestamp() for x in rows])
    r = np.array([x["r"] for x in rows])
    out = []
    for label, sel in (("Y1", t < YEAR1), ("Y2", (t >= YEAR1) & (t < YEAR2)), ("hidden", t >= YEAR2)):
        s = stats(r[sel])
        out.append(f"{label} {s['n']} tr {s['avg']:+.2f}R t={s['t']:.1f}")
    s = stats(r)
    return f"{s['n']} tr {s['win']:.0%} win {s['avg']:+.3f}R {s['total']:+.1f}R | " + " | ".join(out)


def safe_speed(rows: list[dict]) -> str:
    disc = [x for x in rows if x["time"].timestamp() < YEAR2]
    hidden = [x for x in rows if x["time"].timestamp() >= YEAR2]
    best = None
    for scale in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2):
        chance, weeks, _ = walk(disc, ladder(1.5 * scale, 1.0 * scale, 0.5 * scale))
        if chance >= 97.0:
            best = (scale, weeks)
    if best is None:
        return "no safe size"
    scale, weeks = best
    h_chance, h_weeks, _ = walk(hidden, ladder(1.5 * scale, 1.0 * scale, 0.5 * scale))
    return (f"safe ladder top {1.5 * scale:.2f}%: first 24m median {weeks:.0f} wk | hidden 12m {h_chance:.0f}% pass, "
            f"median {h_weeks:.0f} wk")


def main() -> None:
    pairs = []
    for pair in PAIRS:
        try:
            market(pair)
            pairs.append(pair)
        except Exception as exc:
            print(f"{pair}: no history yet ({exc})")
    for plan in PLANS:
        streams = {pair: fx_trades(pair, plan) for pair in pairs}
        pooled = sorted((x for rows in streams.values() for x in rows), key=lambda x: x["time"])
        print(f"\n# Contraction breakout, {plan} target, same settings on {len(pairs)} pairs")
        for pair, rows in streams.items():
            print(f"   {pair}: {period_line(rows)}")
        print(f"   POOLED: {period_line(pooled)}")
    gold = gold_trades()
    print(f"\n# Gold system alone: {period_line(gold)}\n   {safe_speed(gold)}")
    for plan in PLANS:
        fx = [fx_trades(pair, plan) for pair in pairs]
        for max_open, max_usd in ((3, 2), (4, 2), (5, 2)):
            rows = combine([gold, *fx], max_open, max_usd)
            print(f"\n# Gold + FX contraction ({plan}), max {max_open} open, max {max_usd} same dollar bet\n"
                  f"   {period_line(rows)}\n   {safe_speed(rows)}", flush=True)


if __name__ == "__main__":
    main()
