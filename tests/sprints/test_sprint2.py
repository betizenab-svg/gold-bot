import json
from pathlib import Path

from config.database import DB_PATH, get_connection
from src.persistence.schema import SchemaInitializer


def main() -> int:
    connection = get_connection()
    try:
        SchemaInitializer(connection).initialize()

        journal_mode = connection.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal", f"Expected WAL mode, got {journal_mode}"

        candle = {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "timestamp": 1700000000,
            "open": 2000.0,
            "high": 2001.0,
            "low": 1999.5,
            "close": 2000.5,
            "volume": 123.0,
        }

        connection.execute(
            """
            INSERT OR REPLACE INTO market_data (
                symbol, timeframe, timestamp, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                candle["symbol"],
                candle["timeframe"],
                candle["timestamp"],
                candle["open"],
                candle["high"],
                candle["low"],
                candle["close"],
                candle["volume"],
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO market_data (
                symbol, timeframe, timestamp, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                candle["symbol"],
                candle["timeframe"],
                candle["timestamp"],
                candle["open"],
                candle["high"],
                candle["low"],
                candle["close"],
                candle["volume"],
            ),
        )
        connection.commit()

        row_count = connection.execute(
            "SELECT COUNT(*) FROM market_data WHERE symbol = ? AND timeframe = ? AND timestamp = ?;",
            (candle["symbol"], candle["timeframe"], candle["timestamp"]),
        ).fetchone()[0]
        assert row_count == 1, f"Expected 1 candle row, got {row_count}"

        payload = {"bias": "BULLISH"}
        connection.execute(
            """
            INSERT INTO kv_store (key, value, updated_at)
            VALUES (?, ?, strftime('%s','now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at;
            """,
            ("current_context", json.dumps(payload)),
        )
        connection.commit()

        stored_value = connection.execute(
            "SELECT value FROM kv_store WHERE key = ?;",
            ("current_context",),
        ).fetchone()[0]
        assert json.loads(stored_value) == payload, "KV store value mismatch"

        connection.execute(
            """
            INSERT INTO zones (symbol, timeframe, type, price_top, price_bottom, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, strftime('%s','now'));
            """,
            ("XAUUSD", "M1", "OB", 2005.0, 2000.0, "ACTIVE"),
        )
        connection.commit()

        print("Sprint 2 Verification Passed")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
