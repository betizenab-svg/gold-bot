from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from src.domain.candle import Candle
from src.ingestion.factory import get_market_data_client
from src.ingestion.yahoo_client import YahooFinanceClient


def _recent_weekday_base() -> pd.Timestamp:
    base = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=3)
    while base.weekday() >= 5:
        base -= pd.Timedelta(days=1)
    return base


def _repository_getter(last_fetch_timestamp: int):
    def _get_kv(key: str):
        values = {
            "active_provider": "PRIMARY",
            "last_fetch_XAUUSD_H1": str(last_fetch_timestamp),
        }
        return values.get(key)

    return _get_kv


def _build_mock_frame(start_at: pd.Timestamp) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            start_at + pd.Timedelta(hours=1),
            start_at + pd.Timedelta(hours=2),
            start_at + pd.Timedelta(hours=3),
        ],
        tz="UTC",
    )
    return pd.DataFrame(
        {
            "Open": [2900.0, 2901.0, 2902.0],
            "High": [2905.0, 2906.0, 2907.0],
            "Low": [2898.0, 2899.0, 2900.0],
            "Close": [2903.0, 2904.0, 2905.0],
            "Volume": [1000.0, 1100.0, 1200.0],
        },
        index=index,
    )


def main() -> int:
    base_timestamp = _recent_weekday_base()
    last_fetch_timestamp = int(base_timestamp.timestamp())
    expected_start = datetime.fromtimestamp(last_fetch_timestamp, timezone.utc)

    class DummyRepository:
        def __init__(self) -> None:
            self.get_kv = _repository_getter(last_fetch_timestamp)

        def set_kv(self, key: str, value):
            return None

        def log_error(self, provider: str, error_code: str, message: str, timestamp: int):
            return None

    repository = DummyRepository()
    mock_frame = _build_mock_frame(base_timestamp)

    with patch("src.ingestion.yahoo_client.yf.download", return_value=mock_frame) as mocked_download:
        client = get_market_data_client(repository)
        assert isinstance(client, YahooFinanceClient), "Factory should return YahooFinanceClient for PRIMARY"

        candles = client.fetch_latest_candles("XAUUSD", "H1")

    assert mocked_download.call_count == 1, "Expected one Yahoo Finance download call"
    call_kwargs = mocked_download.call_args.kwargs
    assert call_kwargs["tickers"] == "GC=F", "Yahoo ticker mapping should use GC=F"
    assert call_kwargs["start"] == expected_start, "Start parameter should match the incremental watermark"

    assert isinstance(candles, list), "Expected a list of candles"
    assert len(candles) == 3, "Expected exactly 3 candles"
    assert all(isinstance(candle, Candle) for candle in candles), "Expected Candle objects"
    assert all(candle.symbol == "XAUUSD" for candle in candles), "Candles should preserve canonical symbol"
    assert isinstance(candles[0].timestamp, int), "Timestamp should be an integer UTC epoch"

    print("Sprint 3 Refactor (Yahoo Finance) Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
