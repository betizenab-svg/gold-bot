import os
import time
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import Mock, patch

import requests

from config.database import get_connection
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _set_kv(repository: Repository, key: str, value: str) -> None:
    repository.set_kv(key, value)


def _get_kv(repository: Repository, key: str) -> Optional[str]:
    return repository.get_kv(key)


def main() -> int:
    os.environ.setdefault("OANDA_API_KEY", "test_key")
    os.environ.setdefault("OANDA_ACCOUNT_ID", "test_account")

    from src.ingestion.oanda import OandaClient

    connection = get_connection()
    try:
        SchemaInitializer(connection).initialize()
        repository = Repository(connection)

        # Reset circuit breaker state
        repository.set_kv("cb_state", "CLOSED")
        repository.set_kv("cb_failure_count", 0)
        repository.set_kv("cb_cooldown_until", 0)
        repository.set_kv("active_provider", "PRIMARY")

        client = OandaClient(repository)

        symbol = "XAUUSD"
        timeframe = "H1"

        with patch("src.ingestion.oanda.requests.get") as mocked_get:
            mocked_get.side_effect = requests.exceptions.ConnectionError("network down")

            for expected in (1, 2, 3):
                try:
                    client.fetch_latest_candles(symbol, timeframe)
                except Exception:
                    pass

                count = int(_get_kv(repository, "cb_failure_count") or 0)
                assert count == expected, f"Expected failure count {expected}, got {count}"

            assert _get_kv(repository, "cb_state") == "OPEN", "Breaker should be OPEN"
            assert _get_kv(repository, "active_provider") == "SECONDARY", "Provider should switch"

            mocked_get.reset_mock()
            candles = client.fetch_latest_candles(symbol, timeframe)
            assert candles == [], "Breaker should block fetch"
            assert mocked_get.call_count == 0, "API should not be called when breaker open"

            # Force cooldown expiry
            past_ts = int(time.time()) - 10
            _set_kv(repository, "cb_cooldown_until", str(past_ts))

            mocked_get.reset_mock()
            mocked_get.side_effect = None
            mocked_get.return_value = Mock(
                status_code=200,
                json=lambda: {"candles": []},
                text="OK",
            )

            client.fetch_latest_candles(symbol, timeframe)

            assert _get_kv(repository, "cb_state") == "CLOSED", "Breaker should reset to CLOSED"
            assert int(_get_kv(repository, "cb_failure_count") or 0) == 0, "Failure count should reset"

        print("Sprint 4 Circuit Breaker Verified")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
