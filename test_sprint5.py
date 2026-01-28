import os
from typing import List
from unittest.mock import Mock, patch

os.environ.setdefault("TWELVEDATA_API_KEY", "test_key")

from config.database import get_connection
from src.domain.candle import Candle
from src.ingestion.factory import get_market_data_client
from src.ingestion.twelvedata import TwelveDataClient
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _mock_twelvedata_response() -> Mock:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "values": [
            {
                "datetime": "2023-01-01 12:00:00",
                "open": "1800.0",
                "high": "1805.0",
                "low": "1798.0",
                "close": "1802.0",
                "volume": "123",
            }
        ]
    }
    response.text = "OK"
    return response


def main() -> int:
    connection = get_connection()
    try:
        SchemaInitializer(connection).initialize()
        repository = Repository(connection)

        client = TwelveDataClient(repository)

        repository.set_kv("last_fetch_XAUUSD_H1", 0)

        with patch("src.ingestion.twelvedata.requests.get") as mocked_get:
            mocked_get.return_value = _mock_twelvedata_response()
            candles = client.fetch_latest_candles("XAUUSD", "H1")

        assert isinstance(candles, list), "Expected list of candles"
        assert candles, "Expected at least one candle"
        assert all(isinstance(candle, Candle) for candle in candles), "Expected Candle dataclasses"

        first = candles[0]
        assert first.symbol == "XAUUSD", "Symbol should be normalized back to XAUUSD"
        assert isinstance(first.timestamp, int), "Timestamp should be integer Unix epoch"

        repository.set_kv("active_provider", "SECONDARY")
        factory_client = get_market_data_client(repository)
        assert isinstance(factory_client, TwelveDataClient), "Factory should return TwelveDataClient"

        print("Sprint 5 Redundancy & Normalization Verified")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
