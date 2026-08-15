from __future__ import annotations

import os
import sqlite3
from typing import Iterable
from urllib.parse import quote


def build_local_sqlite_uri(db_path: str) -> str:
    absolute_path = os.path.abspath(db_path)
    normalized_path = absolute_path.replace("\\", "/")
    escaped_path = quote(normalized_path, safe="/:")
    # Restrict connection to a local rwc file target with no external URI params.
    return f"file:{escaped_path}?mode=rwc"


class SchemaInitializer:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def initialize(self) -> None:
        statements: Iterable[str] = (
            """
            CREATE TABLE IF NOT EXISTS market_data (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (symbol, timeframe, timestamp)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at INTEGER
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_hash TEXT UNIQUE,
                symbol TEXT,
                type TEXT,
                signal_type TEXT,
                entry REAL,
                entry_price REAL,
                sl REAL,
                sl_price REAL,
                tp1 REAL,
                tp1_price REAL,
                tp2 REAL,
                tp2_price REAL,
                score INTEGER,
                reasoning TEXT,
                timestamp INTEGER,
                telegram_message_id TEXT,
                telegram_chat_id TEXT,
                closure_reason TEXT,
                created_at INTEGER,
                status TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                timeframe TEXT,
                type TEXT,
                price_top REAL,
                price_bottom REAL,
                status TEXT,
                created_at INTEGER
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT,
                error_code TEXT,
                message TEXT,
                timestamp INTEGER
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS market_data_timestamp
            ON market_data (timestamp);
            """,
            """
            CREATE INDEX IF NOT EXISTS signals_status
            ON signals (status, signal_hash);
            """,
            """
            CREATE INDEX IF NOT EXISTS zones_status
            ON zones (status);
            """,
            """
            CREATE INDEX IF NOT EXISTS kv_store_key
            ON kv_store (key);
            """,
        )
        cursor = self.connection.cursor()
        for statement in statements:
            cursor.execute(statement)
        self._ensure_column(cursor, "signals", "signal_type", "TEXT")
        self._ensure_column(cursor, "signals", "entry_price", "REAL")
        self._ensure_column(cursor, "signals", "sl_price", "REAL")
        self._ensure_column(cursor, "signals", "tp1_price", "REAL")
        self._ensure_column(cursor, "signals", "tp2_price", "REAL")
        self._ensure_column(cursor, "signals", "score", "INTEGER")
        self._ensure_column(cursor, "signals", "reasoning", "TEXT")
        self._ensure_column(cursor, "signals", "timestamp", "INTEGER")
        self._ensure_column(cursor, "signals", "telegram_message_id", "TEXT")
        self._ensure_column(cursor, "signals", "telegram_chat_id", "TEXT")
        self._ensure_column(cursor, "signals", "closure_reason", "TEXT")
        self._ensure_column(cursor, "signals", "order_type", "TEXT")
        self._ensure_column(cursor, "signals", "strategy", "TEXT")
        self._ensure_column(cursor, "signals", "mfe_r", "REAL")
        self._ensure_column(cursor, "signals", "mae_r", "REAL")
        self.connection.commit()

    def _ensure_column(
        self,
        cursor: sqlite3.Cursor,
        table_name: str,
        column_name: str,
        column_type: str,
    ) -> None:
        rows = cursor.execute(f"PRAGMA table_info({table_name});").fetchall()
        existing_columns = {row[1] for row in rows}
        if column_name in existing_columns:
            return

        cursor.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type};"
        )
