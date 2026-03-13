import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.backtest.csv_reader import CSVDataClient
from src.backtest.engine import BacktestEngine


def _write_mock_history_csv(filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)

    with filepath.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])

        for index in range(198):
            close_price = 100.0 + index
            open_price = close_price - 0.4
            writer.writerow(
                [
                    (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                    f"{open_price:.2f}",
                    f"{close_price + 0.4:.2f}",
                    f"{open_price - 0.4:.2f}",
                    f"{close_price:.2f}",
                    "100.0",
                ]
            )

        writer.writerow(
            [
                (start + timedelta(minutes=198)).isoformat().replace("+00:00", "Z"),
                "299.00",
                "300.00",
                "288.00",
                "293.00",
                "100.0",
            ]
        )
        writer.writerow(
            [
                (start + timedelta(minutes=199)).isoformat().replace("+00:00", "Z"),
                "292.00",
                "302.00",
                "291.00",
                "301.00",
                "100.0",
            ]
        )
        writer.writerow(
            [
                (start + timedelta(minutes=200)).isoformat().replace("+00:00", "Z"),
                "301.50",
                "304.00",
                "300.00",
                "303.00",
                "120.0",
            ]
        )

        for index in range(201, 250):
            close_price = 303.0 + ((index - 200) * 2.0)
            writer.writerow(
                [
                    (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                    f"{close_price - 1.0:.2f}",
                    f"{close_price + 1.0:.2f}",
                    f"{close_price - 2.0:.2f}",
                    f"{close_price:.2f}",
                    "140.0",
                ]
            )


def main() -> None:
    filepath = Path("data/mock_history.csv")
    _write_mock_history_csv(filepath)

    try:
        client = CSVDataClient()
        candles = client.load_data(str(filepath))
        assert len(candles) == 250

        engine = BacktestEngine(candles)
        engine.run_simulation()
        assert len(engine.trade_history) >= 1

        engine.generate_report()
        print("Sprint 34 Backtesting Engine Verified")
    finally:
        if filepath.exists():
            filepath.unlink()


if __name__ == "__main__":
    main()
