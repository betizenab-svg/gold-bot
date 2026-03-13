from __future__ import annotations

import argparse
import os
import sys
from itertools import product
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from src.backtest.csv_reader import CSVDataClient
from src.backtest.engine import BacktestEngine
from src.strategies.big_bulls_bears import BigBullsBearsStrategy

ATR_GRID = [0.5, 0.75, 1.0]
PIN_GRID = [0.60, 0.66, 0.70]
VALUE_AREA_GRID = [14, 20, 30]


def _format_table(top_rows: list[dict[str, Any]]) -> str:
    headers = [
        "Rank",
        "ATR_SL_MULT",
        "PIN_BAR_TAIL",
        "VALUE_AREA_SMA",
        "Total Trades",
        "Win Rate %",
        "Net PnL",
    ]
    lines = [" | ".join(headers), " | ".join(["---"] * len(headers))]
    for idx, row in enumerate(top_rows, start=1):
        lines.append(
            " | ".join(
                [
                    str(idx),
                    f"{row['atr_sl_multiplier']:.2f}",
                    f"{row['pin_bar_tail_ratio']:.2f}",
                    str(row["value_area_sma"]),
                    str(int(row["total_trades"])),
                    f"{float(row['win_rate_pct']):.2f}",
                    f"{float(row['net_pnl']):.2f}",
                ]
            )
        )
    return "\n".join(lines)


def run_grid_search(
    candles: list[Any],
    initial_balance: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for atr_sl_multiplier, pin_bar_tail_ratio, value_area_sma in product(
        ATR_GRID,
        PIN_GRID,
        VALUE_AREA_GRID,
    ):
        # Inject tuning constants for this simulation run.
        settings.ATR_SL_MULTIPLIER = float(atr_sl_multiplier)
        settings.PIN_BAR_TAIL_RATIO = float(pin_bar_tail_ratio)
        settings.VALUE_AREA_SMA = int(value_area_sma)

        strategy = BigBullsBearsStrategy(value_period=int(value_area_sma))

        # Fresh engine per run guarantees full state reset.
        engine = BacktestEngine(
            candles=list(candles),
            strategy=strategy,
            initial_balance=float(initial_balance),
        )
        engine.run_simulation()
        report = engine.generate_report()

        final_balance = float(report.get("final_balance", initial_balance))
        results.append(
            {
                "atr_sl_multiplier": float(atr_sl_multiplier),
                "pin_bar_tail_ratio": float(pin_bar_tail_ratio),
                "value_area_sma": int(value_area_sma),
                "total_trades": int(report.get("total_trades", 0)),
                "win_rate_pct": float(report.get("win_rate_pct", 0.0)),
                "net_pnl": round(final_balance - float(initial_balance), 2),
            }
        )

    return sorted(results, key=lambda item: float(item["net_pnl"]), reverse=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optimize strategy parameters via grid-search backtesting")
    parser.add_argument(
        "filepath",
        nargs="?",
        default=str(PROJECT_ROOT / "data" / "historical_test.csv"),
        help="Path to OHLCV CSV file",
    )
    parser.add_argument(
        "--initial-balance",
        type=float,
        default=10000.0,
        help="Initial account balance for each simulation run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Load once for all 27 combinations to keep optimization fast.
    candles = CSVDataClient().load_data(args.filepath)
    ranked_results = run_grid_search(candles, initial_balance=float(args.initial_balance))

    top_five = ranked_results[:5]
    print(_format_table(top_five))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
