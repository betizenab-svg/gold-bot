from __future__ import annotations

import sqlite3
from typing import Iterable


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
                entry REAL,
                sl REAL,
                tp1 REAL,
                tp2 REAL,
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
        )
        cursor = self.connection.cursor()
        for statement in statements:
            cursor.execute(statement)
        self.connection.commit()
