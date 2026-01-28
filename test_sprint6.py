import os
from datetime import datetime, timezone
from unittest.mock import Mock, patch

os.environ.setdefault("OANDA_API_KEY", "test_key")
os.environ.setdefault("OANDA_ACCOUNT_ID", "test_account")

from src.domain.candle import Candle
from src.ingestion.oanda import OandaClient
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.validation.validator import DataValidator
from config.database import get_connection


def _mock_oanda_response(candles_payload):
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"candles": candles_payload}
    response.text = "OK"
    return response


def _iso_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    validator = DataValidator()

    base_ts = 1700000000
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
        client = OandaClient(repository)

        candles_payload = [
            {
                "complete": True,
                "volume": 120,
                "time": _iso_from_ts(base_ts),
                "mid": {"o": "2000", "h": "2001", "l": "1999", "c": "2000.5"},
            },
            {
                "complete": True,
                "volume": 0,
                "time": _iso_from_ts(base_ts + 60),
                "mid": {"o": "2000", "h": "2001", "l": "1999", "c": "2000.5"},
            },
            {
                "complete": True,
                "volume": 50,
                "time": _iso_from_ts(base_ts + 120),
                "mid": {"o": "2000", "h": "1990", "l": "1995", "c": "1992"},
            },
        ]

        with patch("src.ingestion.oanda.requests.get") as mocked_get:
            mocked_get.return_value = _mock_oanda_response(candles_payload)
            result = client.fetch_latest_candles("XAUUSD", "M1")

        assert len(result) == 1, f"Expected 1 valid candle, got {len(result)}"
        assert result[0].timestamp == base_ts, "Valid candle mismatch"

        print("Sprint 6 Validation & Normalization Verified")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
