import os
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import Mock, patch

from config.database import get_connection
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _iso_from_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_candles(start_ts: int, count: int, step: int = 60) -> List[Dict[str, Any]]:
    candles = []
    for i in range(count):
        ts = start_ts + i * step
        candles.append(
            {
                "complete": True,
                "volume": 100 + i,
                "time": _iso_from_timestamp(ts),
                "mid": {
                    "o": "2000.0",
                    "h": "2001.0",
                    "l": "1999.5",
                    "c": "2000.5",
                },
            }
        )
    return candles


def _mock_response(payload: Dict[str, Any]) -> Mock:
    response = Mock()
    response.status_code = 200
    response.json.return_value = payload
    response.text = "OK"
    return response


def main() -> int:
    os.environ.setdefault("OANDA_API_KEY", "test_key")
    os.environ.setdefault("OANDA_ACCOUNT_ID", "test_account")

    from src.ingestion.oanda import OandaClient

    connection = get_connection()
    try:
        SchemaInitializer(connection).initialize()
        repository = Repository(connection)
        client = OandaClient(repository)

        symbol = "XAUUSD_TEST"
        timeframe = "H1"

        connection.execute("DELETE FROM market_data WHERE symbol = ?;", (symbol,))
        connection.execute("DELETE FROM kv_store WHERE key = ?;", (f"last_fetch_{symbol}_{timeframe}",))
        connection.commit()

        base_timestamp = 1700000000
        first_candles = _build_candles(base_timestamp - 9 * 60, 10)
        second_candles = _build_candles(base_timestamp + 60, 2)

        with patch("src.ingestion.oanda.requests.get") as mocked_get:
            mocked_get.side_effect = [
                _mock_response({"candles": first_candles}),
                _mock_response({"candles": second_candles}),
            ]

            before_call = datetime.now(timezone.utc)
            candles = client.fetch_latest_candles(symbol, timeframe)
            after_call = datetime.now(timezone.utc)

            assert mocked_get.call_count == 1, "Expected first API call"
            call_kwargs = mocked_get.call_args.kwargs
            from_param = call_kwargs["params"]["from"]
            from_ts = int(datetime.fromisoformat(from_param.replace("Z", "+00:00")).timestamp())
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
            from_param = call_kwargs["params"]["from"]
            from_ts = int(datetime.fromisoformat(from_param.replace("Z", "+00:00")).timestamp())
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
