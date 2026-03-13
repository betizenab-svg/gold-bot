from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from src.domain.candle import Candle


class CSVDataClient:
    """Load OHLCV history from CSV into canonical Candle objects."""

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "M1") -> None:
        self.symbol = symbol
        self.timeframe = timeframe

    def load_data(self, filepath: str) -> list[Candle]:
        path = Path(filepath)
        candles: list[Candle] = []

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                timestamp = self._parse_timestamp(str(row["Date"]))
                candles.append(
                    Candle(
                        symbol=self.symbol,
                        timeframe=self.timeframe,
                        timestamp=timestamp,
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row["Volume"]),
                    )
                )

        candles.sort(key=lambda candle: candle.timestamp)
        return candles

    @staticmethod
    def _parse_timestamp(raw_value: str) -> int:
        normalized = raw_value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M",
            ):
                try:
                    parsed = datetime.strptime(normalized, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(f"Unsupported CSV date format: {raw_value}")

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return int(parsed.timestamp())
