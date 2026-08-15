"""Evidence-based tuning report from realized signal telemetry.

Reads the signals table (statuses + MFE/MAE excursions in R) and prints, per
strategy: sample size, expectancy, profit factor, and how far losers ran in
your favor / winners ran against you. Ends with concrete knob recommendations.

Usage:
    python scripts/calibrate_from_history.py [path/to/trading_engine.db]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from statistics import median
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.settings import DB_PATH  # noqa: E402

# Realized R per terminal status (TP1=1.5R half off, TP2=3R, BE runner flat).
OUTCOME_R = {
    "CLOSED_TP2": 2.25,
    "CLOSED_BE": 0.75,
    "CLOSED_SL": -1.0,
    "CLOSED_TIME": 0.0,
}

MIN_SAMPLE_FOR_ADVICE = 10


def analyze(db_path: str) -> dict[str, Any]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT COALESCE(strategy, 'UNKNOWN'), status,
                   COALESCE(mfe_r, 0.0), COALESCE(mae_r, 0.0)
            FROM signals
            WHERE status IN ('CLOSED_TP2', 'CLOSED_BE', 'CLOSED_SL', 'CLOSED_TIME');
            """
        ).fetchall()
    finally:
        connection.close()

    strategies: dict[str, dict[str, Any]] = {}
    for strategy, status, mfe_r, mae_r in rows:
        stats = strategies.setdefault(
            str(strategy),
            {
                "trades": 0,
                "r_values": [],
                "gross_win": 0.0,
                "gross_loss": 0.0,
                "loser_mfe": [],
                "winner_mae": [],
            },
        )
        status = str(status).upper()
        r_value = OUTCOME_R.get(status)
        if r_value is None:
            continue
        stats["trades"] += 1
        stats["r_values"].append(r_value)
        if r_value > 0:
            stats["gross_win"] += r_value
            stats["winner_mae"].append(float(mae_r))
        elif r_value < 0:
            stats["gross_loss"] += abs(r_value)
            stats["loser_mfe"].append(float(mfe_r))

    report: dict[str, Any] = {"strategies": {}, "recommendations": []}
    for strategy, stats in strategies.items():
        trades = stats["trades"]
        if trades == 0:
            continue
        expectancy = sum(stats["r_values"]) / trades
        profit_factor = (
            stats["gross_win"] / stats["gross_loss"] if stats["gross_loss"] > 0 else float("inf")
        )
        loser_mfe_median = median(stats["loser_mfe"]) if stats["loser_mfe"] else None
        winner_mae_median = median(stats["winner_mae"]) if stats["winner_mae"] else None

        report["strategies"][strategy] = {
            "trades": trades,
            "expectancy_r": round(expectancy, 3),
            "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
            "median_loser_mfe_r": round(loser_mfe_median, 3) if loser_mfe_median is not None else None,
            "median_winner_mae_r": round(winner_mae_median, 3) if winner_mae_median is not None else None,
        }

        if trades < MIN_SAMPLE_FOR_ADVICE:
            continue

        if expectancy < 0:
            report["recommendations"].append(
                f"{strategy}: expectancy {expectancy:+.2f}R over {trades} trades - "
                "quarantine candidate; review or disable this strategy."
            )
        if loser_mfe_median is not None and loser_mfe_median >= 1.0:
            report["recommendations"].append(
                f"{strategy}: losers ran a median {loser_mfe_median:.2f}R in your favor "
                "before dying - TP1 is too far or breakeven should come earlier."
            )
        if winner_mae_median is not None and winner_mae_median < 0.4:
            report["recommendations"].append(
                f"{strategy}: winners took only {winner_mae_median:.2f}R median heat - "
                "the stop is wider than needed; consider lowering SL_MIN_ATR_MULT or "
                "ATR_SL_MULTIPLIER one notch."
            )
        if winner_mae_median is not None and winner_mae_median > 0.85:
            report["recommendations"].append(
                f"{strategy}: winners took {winner_mae_median:.2f}R median heat - "
                "the stop is barely surviving; consider widening ATR_SL_MULTIPLIER."
            )

    if not report["recommendations"]:
        report["recommendations"].append(
            "Not enough closed-trade evidence yet - keep collecting "
            f"(advice starts at {MIN_SAMPLE_FOR_ADVICE} closed trades per strategy)."
        )
    return report


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return 1

    report = analyze(str(db_path))
    print("Strategy performance (realized, in R):")
    if not report["strategies"]:
        print("  No closed trades recorded yet.")
    for strategy, stats in report["strategies"].items():
        pf = stats["profit_factor"]
        print(
            f"  {strategy}: {stats['trades']} trades | "
            f"expectancy {stats['expectancy_r']:+.2f}R | "
            f"profit factor {pf if pf is not None else 'inf'} | "
            f"loser MFE {stats['median_loser_mfe_r']} | "
            f"winner MAE {stats['median_winner_mae_r']}"
        )
    print("\nRecommendations:")
    for line in report["recommendations"]:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
