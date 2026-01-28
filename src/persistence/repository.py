from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

from src.domain.candle import Candle


class Repository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_candle(self, candle: Dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO market_data (
                symbol, timeframe, timestamp, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                candle.get("symbol"),
                candle.get("timeframe"),
                candle.get("timestamp"),
                candle.get("open"),
                candle.get("high"),
                candle.get("low"),
                candle.get("close"),
                candle.get("volume"),
            ),
        )
        self.connection.commit()

    def save_candles(self, candles: Iterable[Candle]) -> None:
        payload = [
            (
                candle.symbol,
                candle.timeframe,
                candle.timestamp,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
            for candle in candles
        ]
        if not payload:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO market_data (
                symbol, timeframe, timestamp, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            payload,
        )
        self.connection.commit()

    def get_kv(self, key: str) -> Optional[str]:
        cursor = self.connection.execute(
            "SELECT value FROM kv_store WHERE key = ?;",
            (key,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def set_kv(self, key: str, value: Any) -> None:
        if isinstance(value, (dict, list)):
            stored_value = json.dumps(value)
        else:
            stored_value = str(value)
        updated_at = int(time.time())
        self.connection.execute(
            """
            INSERT INTO kv_store (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at;
            """,
            (key, stored_value, updated_at),
        )
        self.connection.commit()

    def update_watermark(self, symbol: str, timeframe: str, timestamp: int) -> None:
        key = f"last_fetch_{symbol}_{timeframe}"
        self.set_kv(key, int(timestamp))

    def log_signal(self, signal: Dict[str, Any]) -> bool:
        try:
            self.connection.execute(
                """
                INSERT INTO signals (
                    signal_hash, symbol, type, entry, sl, tp1, tp2, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    signal.get("signal_hash"),
                    signal.get("symbol"),
                    signal.get("type"),
                    signal.get("entry"),
                    signal.get("sl"),
                    signal.get("tp1"),
                    signal.get("tp2"),
                    signal.get("created_at"),
                    signal.get("status"),
                ),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_active_zones(self, symbol: str) -> List[Dict[str, Any]]:
        cursor = self.connection.execute(
            """
            SELECT id, symbol, timeframe, type, price_top, price_bottom, status, created_at
            FROM zones
            WHERE symbol = ? AND status = 'ACTIVE'
            ORDER BY created_at DESC;
            """,
            (symbol,),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "symbol": row[1],
                "timeframe": row[2],
                "type": row[3],
                "price_top": row[4],
                "price_bottom": row[5],
                "status": row[6],
                "created_at": row[7],
            }
            for row in rows
        ]
