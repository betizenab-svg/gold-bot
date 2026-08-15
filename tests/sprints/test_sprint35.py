from __future__ import annotations

import os
import sqlite3
import tempfile
import weakref
import gc
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import yfinance as yf

from src.ingestion.yahoo_client import YahooFinanceClient
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _create_test_db() -> str:
    fd, db_path = tempfile.mkstemp(prefix="sprint35_", suffix=".db")
    os.close(fd)
    return db_path


def _init_schema(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA busy_timeout=3000;")
        SchemaInitializer(connection).initialize()
    finally:
        connection.close()


@pytest.fixture
def db_path():
    path = _create_test_db()
    _init_schema(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_database_indexes(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index';
            """
        ).fetchall()
    finally:
        connection.close()

    indexes = {str(row[0]) for row in rows}
    assert "market_data_timestamp" in indexes, "Missing index: market_data_timestamp"
    assert "signals_status" in indexes, "Missing index: signals_status"
    assert "zones_status" in indexes, "Missing index: zones_status"


def test_connection_closure(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    repository = Repository(connection)
    repository.get_active_zones("XAUUSD")
    repository.close()

    os.remove(db_path)


def test_memory_deallocation(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    repository = Repository(connection)
    client = YahooFinanceClient(repository=repository)

    # Candles must lie in the past: the client drops still-forming bars.
    start = datetime.now(timezone.utc) - timedelta(minutes=5010)
    index = pd.date_range(start=start, periods=5000, freq="min", tz="UTC")

    tracker: dict[str, weakref.ReferenceType[pd.DataFrame]] = {}

    def fake_download(*args, **kwargs):
        frame = pd.DataFrame(
            {
                "Open": [2000.0] * len(index),
                "High": [2001.0] * len(index),
                "Low": [1999.0] * len(index),
                "Close": [2000.5] * len(index),
                "Volume": [100.0] * len(index),
            },
            index=index,
        )
        tracker["df_ref"] = weakref.ref(frame)
        return frame

    original_download = yf.download
    try:
        yf.download = fake_download
        candles = client.fetch_latest_candles("XAUUSD", "M1")
        assert candles, "Expected candle payload from mocked DataFrame"
    finally:
        yf.download = original_download
        repository.close()

    gc.collect()
    assert "df_ref" in tracker, "DataFrame weak reference tracker was not created"
    assert tracker["df_ref"]() is None, "DataFrame should be out of scope after fetch_latest_candles"


def main() -> int:
    db_path = _create_test_db()
    try:
        _init_schema(db_path)
        test_database_indexes(db_path)

        # Connection-release test deletes the DB file to prove no file lock remains.
        test_connection_closure(db_path)

        # Recreate DB for remaining tests.
        _init_schema(db_path)
        test_memory_deallocation(db_path)
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)

    print("Sprint 35 Code Optimization Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
