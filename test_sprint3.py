import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import Mock, patch

import pandas as pd

from config.database import get_connection
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


TEST_BASE_TIMESTAMP = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())


def _build_candles(start_ts: int, count: int, step: int = 60) -> List[Dict[str, Any]]:
    candles = []
    for i in range(count):
        ts = start_ts + i * step
        candles.append(
            {
                "timestamp": ts,
                "Open": 2000.0,
                "High": 2001.0,
                "Low": 1999.5,
                "Close": 2000.5,
                "Volume": 100 + i,
            }
        )
    return candles


def _mock_frame(payload: List[Dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(payload)
    frame.index = pd.to_datetime(frame.pop("timestamp"), unit="s", utc=True)
    return frame


def main() -> int:
    from src.ingestion.yahoo_client import YahooFinanceClient

    connection = get_connection()
    try:
        SchemaInitializer(connection).initialize()
        repository = Repository(connection)
        client = YahooFinanceClient(repository)

        symbol = "XAUUSD_TEST"
        timeframe = "H1"

        connection.execute("DELETE FROM market_data WHERE symbol = ?;", (symbol,))
        connection.execute("DELETE FROM kv_store WHERE key = ?;", (f"last_fetch_{symbol}_{timeframe}",))
        connection.execute("DELETE FROM kv_store WHERE key = 'last_processed_timestamp';")
        connection.commit()

        base_timestamp = TEST_BASE_TIMESTAMP
        first_candles = _build_candles(base_timestamp - 9 * 60, 10)
        second_candles = _build_candles(base_timestamp + 60, 2)

        with patch("src.ingestion.yahoo_client.yf.download") as mocked_get:
            mocked_get.side_effect = [
                _mock_frame(first_candles),
                _mock_frame(second_candles),
            ]

            before_call = datetime.now(timezone.utc)
            candles = client.fetch_latest_candles(symbol, timeframe)
            after_call = datetime.now(timezone.utc)

            assert mocked_get.call_count == 1, "Expected first API call"
            call_kwargs = mocked_get.call_args.kwargs
            from_ts = int(call_kwargs["start"].timestamp())
            expected_start = int((before_call.timestamp()) - 24 * 3600)
            expected_end = int((after_call.timestamp()) - 24 * 3600)
            assert expected_start - 5 <= from_ts <= expected_end + 5, "First run should request ~24h ago"

            repository.save_candles(candles)
            latest_timestamp = max(candle.timestamp for candle in candles)
            repository.update_watermark(symbol, timeframe, latest_timestamp)

            watermark_key = f"last_fetch_{symbol}_{timeframe}"
            watermark_value = repository.get_kv(watermark_key)
            assert watermark_value == str(base_timestamp), "Watermark should store latest timestamp T"

            count = connection.execute(
                "SELECT COUNT(*) FROM market_data WHERE symbol = ?;",
                (symbol,),
            ).fetchone()[0]
            assert count == 10, f"Expected 10 candles, got {count}"

            candles_second = client.fetch_latest_candles(symbol, timeframe)
            assert mocked_get.call_count == 2, "Expected second API call"
            call_kwargs = mocked_get.call_args.kwargs
            from_ts = int(call_kwargs["start"].timestamp())
            assert from_ts == base_timestamp, "Second run should request from watermark T"

            repository.save_candles(candles_second)

            count_after = connection.execute(
                "SELECT COUNT(*) FROM market_data WHERE symbol = ?;",
                (symbol,),
            ).fetchone()[0]
            assert count_after == 12, f"Expected 12 candles, got {count_after}"

        print("Sprint 3 Incremental Fetching Verified")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
