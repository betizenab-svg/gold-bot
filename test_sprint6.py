import os
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from src.domain.candle import Candle
from src.ingestion.yahoo_client import YahooFinanceClient
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.validation.validator import DataValidator
from config.database import get_connection


TEST_BASE_TIMESTAMP = int(datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc).timestamp())


def _mock_yahoo_frame(candles_payload):
    frame = pd.DataFrame(candles_payload)
    frame.index = pd.to_datetime(frame.pop("timestamp"), unit="s", utc=True)
    return frame


def main() -> int:
    validator = DataValidator()

    base_ts = TEST_BASE_TIMESTAMP
    valid_candle = Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=base_ts,
        open=2000.0,
        high=2001.0,
        low=1999.0,
        close=2000.5,
        volume=120.0,
    )
    ghost_tick = Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=base_ts + 60,
        open=2000.0,
        high=2001.0,
        low=1999.0,
        close=2000.5,
        volume=0.0,
    )
    bad_price = Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=base_ts + 120,
        open=2000.0,
        high=1990.0,
        low=1995.0,
        close=1992.0,
        volume=50.0,
    )

    assert validator.validate_candle(valid_candle) is True, "Valid candle rejected"
    assert validator.validate_candle(ghost_tick) is False, "Ghost tick not rejected"
    assert validator.validate_candle(bad_price) is False, "Bad price not rejected"

    gap_candles = [
        Candle("XAUUSD", "M1", base_ts, 1, 2, 1, 2, 10),
        Candle("XAUUSD", "M1", base_ts + 60, 1, 2, 1, 2, 10),
        Candle("XAUUSD", "M1", base_ts + 180, 1, 2, 1, 2, 10),
    ]
    gaps = validator.detect_gaps(gap_candles, "M1")
    assert gaps == [base_ts + 120], f"Unexpected gaps: {gaps}"

    connection = get_connection()
    try:
        SchemaInitializer(connection).initialize()
        repository = Repository(connection)
        repository.set_kv("last_fetch_XAUUSD_M1", 0)
        client = YahooFinanceClient(repository)

        candles_payload = [
            {
                "timestamp": base_ts,
                "Open": 2000.0,
                "High": 2001.0,
                "Low": 1999.0,
                "Close": 2000.5,
                "Volume": 120.0,
            },
            {
                "timestamp": base_ts + 60,
                "Open": 2000.0,
                "High": 2001.0,
                "Low": 1999.0,
                "Close": 2000.5,
                "Volume": 0.0,
            },
            {
                "timestamp": base_ts + 120,
                "Open": 2000.0,
                "High": 1990.0,
                "Low": 1995.0,
                "Close": 1992.0,
                "Volume": 50.0,
            },
        ]

        with patch("src.ingestion.yahoo_client.yf.download") as mocked_get:
            mocked_get.return_value = _mock_yahoo_frame(candles_payload)
            result = client.fetch_latest_candles("XAUUSD", "M1")

        assert len(result) == 1, f"Expected 1 valid candle, got {len(result)}"
        assert result[0].timestamp == base_ts, "Valid candle mismatch"

        print("Sprint 6 Validation & Normalization Verified")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
