from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

from src.domain.candle import Candle
from src.domain.signal import Signal


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

    def get_recent_candles(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> List[Candle]:
        if limit <= 0:
            return []

        cursor = self.connection.execute(
            """
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume
            FROM (
                SELECT symbol, timeframe, timestamp, open, high, low, close, volume
                FROM market_data
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
            )
            ORDER BY timestamp ASC;
            """,
            (symbol, timeframe, limit),
        )
        rows = cursor.fetchall()
        return [
            Candle(
                symbol=row[0],
                timeframe=row[1],
                timestamp=int(row[2]),
                open=float(row[3]),
                high=float(row[4]),
                low=float(row[5]),
                close=float(row[6]),
                volume=float(row[7]),
            )
            for row in rows
        ]

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

    def is_signal_duplicate(self, signal_hash: str) -> bool:
        cursor = self.connection.execute(
            "SELECT 1 FROM signals WHERE signal_hash = ? LIMIT 1;",
            (signal_hash,),
        )
        return cursor.fetchone() is not None

    def save_signal(self, signal: Signal) -> None:
        self.connection.execute(
            """
            INSERT INTO signals (
                signal_hash,
                symbol,
                type,
                signal_type,
                entry,
                entry_price,
                sl,
                sl_price,
                tp1,
                tp1_price,
                tp2,
                tp2_price,
                score,
                reasoning,
                timestamp,
                telegram_message_id,
                telegram_chat_id,
                closure_reason,
                created_at,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                signal.signal_hash,
                signal.symbol,
                signal.signal_type,
                signal.signal_type,
                signal.entry_price,
                signal.entry_price,
                signal.sl_price,
                signal.sl_price,
                signal.tp1_price,
                signal.tp1_price,
                signal.tp2_price,
                signal.tp2_price,
                signal.score,
                signal.reasoning,
                signal.timestamp,
                signal.telegram_message_id,
                signal.telegram_chat_id,
                signal.closure_reason,
                signal.timestamp,
                "PENDING",
            ),
        )
        self.connection.commit()

    def update_signal_telegram_metadata(
        self,
        signal_hash: str,
        telegram_message_id: str,
        telegram_chat_id: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE signals
            SET telegram_message_id = ?, telegram_chat_id = ?
            WHERE signal_hash = ?;
            """,
            (str(telegram_message_id), str(telegram_chat_id), signal_hash),
        )
        self.connection.commit()

    def update_signal_closure(
        self,
        signal_hash: str,
        closure_reason: str,
        status: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE signals
            SET closure_reason = ?, status = ?
            WHERE signal_hash = ?;
            """,
            (closure_reason, status, signal_hash),
        )
        self.connection.commit()

    def save_zone(self, zone: Dict[str, Any]) -> None:
        created_at = int(zone.get("created_at", int(time.time())))
        self.connection.execute(
            """
            INSERT INTO zones (
                symbol, timeframe, type, price_top, price_bottom, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                zone.get("symbol"),
                zone.get("timeframe"),
                zone.get("type"),
                zone.get("price_top"),
                zone.get("price_bottom"),
                zone.get("status"),
                created_at,
            ),
        )
        self.connection.commit()

    def get_active_zones(self, symbol: str) -> List[Dict[str, Any]]:
        cursor = self.connection.execute(
            """
            SELECT id, symbol, timeframe, type, price_top, price_bottom, status, created_at
            FROM zones
            WHERE symbol = ? AND status IN ('ACTIVE', 'UNMITIGATED')
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

    def update_zone_statuses(self, updated_zones: List[Dict[str, Any]]) -> None:
        payload = [
            (
                str(zone.get("status")),
                int(zone.get("id")),
            )
            for zone in updated_zones
            if zone.get("id") is not None and zone.get("status") is not None
        ]
        if not payload:
            return

        self.connection.executemany(
            """
            UPDATE zones
            SET status = ?
            WHERE id = ?;
            """,
            payload,
        )
        self.connection.commit()

    def log_error(self, provider: str, error_code: str, message: str, timestamp: int) -> None:
        self.connection.execute(
            """
            INSERT INTO errors (provider, error_code, message, timestamp)
            VALUES (?, ?, ?, ?);
            """,
            (provider, error_code, message, timestamp),
        )
        self.connection.commit()
