from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.csv_reader import CSVDataClient
from src.backtest.engine import BacktestEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline XAUUSD CSV backtest.")
    parser.add_argument(
        "filepath",
        nargs="?",
        default=str(PROJECT_ROOT / "data" / "historical_test.csv"),
        help="Path to OHLCV CSV file",
    )
    args = parser.parse_args()

    client = CSVDataClient()
    candles = client.load_data(args.filepath)
    engine = BacktestEngine(candles)
    engine.run_simulation()
    engine.generate_report()


if __name__ == "__main__":
    main()
