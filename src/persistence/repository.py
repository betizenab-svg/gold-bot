from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional

from src.domain.candle import Candle
from src.domain.signal import Signal


class Repository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection: Optional[sqlite3.Connection] = None
        self._db_path: Optional[str] = None
        self._shared_connection_mode = False

        db_path = self._extract_db_path(connection)
        if db_path in {"", ":memory:"}:
            self.connection = connection
            self._shared_connection_mode = True
            self._configure_connection(connection)
            return

        self._db_path = db_path
        self._configure_connection(connection)
        connection.close()

    def _extract_db_path(self, connection: sqlite3.Connection) -> str:
        try:
            rows = connection.execute("PRAGMA database_list;").fetchall()
        except sqlite3.Error:
            return ""
        except Exception:
            # Non-sqlite doubles (test mocks) fall back to shared-connection mode.
            return ""

        if not isinstance(rows, list) or not rows:
            return ""

        try:
            raw_path = rows[0][2]
        except (TypeError, IndexError, KeyError):
            return ""

        if not isinstance(raw_path, str):
            return ""
        return raw_path.strip()

    def _configure_connection(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA foreign_keys=ON;")
        connection.execute("PRAGMA busy_timeout=3000;")

    def _open_connection(self) -> sqlite3.Connection:
        if self._shared_connection_mode:
            if self.connection is None:
                raise RuntimeError("Repository connection is closed")
            return self.connection

        if not self._db_path:
            raise RuntimeError("Repository database path is not configured")

        connection = sqlite3.connect(self._db_path)
        self._configure_connection(connection)
        return connection

    def _fetchall(self, query: str, params: Iterable[Any] = ()) -> List[tuple[Any, ...]]:
        if self._shared_connection_mode:
            connection = self._open_connection()
            with closing(connection.cursor()) as cursor:
                cursor.execute(query, tuple(params))
                return cursor.fetchall()

        with closing(self._open_connection()) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(query, tuple(params))
                return cursor.fetchall()

    def _fetchone(self, query: str, params: Iterable[Any] = ()) -> Optional[tuple[Any, ...]]:
        if self._shared_connection_mode:
            connection = self._open_connection()
            with closing(connection.cursor()) as cursor:
                cursor.execute(query, tuple(params))
                return cursor.fetchone()

        with closing(self._open_connection()) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(query, tuple(params))
                return cursor.fetchone()

    def _execute(self, query: str, params: Iterable[Any] = ()) -> None:
        if self._shared_connection_mode:
            connection = self._open_connection()
            with connection:
                with closing(connection.cursor()) as cursor:
                    cursor.execute(query, tuple(params))
            return

        with closing(self._open_connection()) as connection:
            with connection:
                with closing(connection.cursor()) as cursor:
                    cursor.execute(query, tuple(params))

    def _executemany(self, query: str, payload: List[tuple[Any, ...]]) -> None:
        if not payload:
            return

        if self._shared_connection_mode:
            connection = self._open_connection()
            connection.executemany(query, payload)
            connection.commit()
            return

        with closing(self._open_connection()) as connection:
            with connection:
                with closing(connection.cursor()) as cursor:
                    cursor.executemany(query, payload)

    def close(self) -> None:
        if self._shared_connection_mode and self.connection is not None:
            self.connection.close()
            self.connection = None

    def save_candle(self, candle: Dict[str, Any]) -> None:
        self._execute(
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
        self._executemany(
            """
            INSERT OR REPLACE INTO market_data (
                symbol, timeframe, timestamp, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            payload,
        )

    def get_recent_candles(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> List[Candle]:
        if limit <= 0:
            return []

        rows = self._fetchall(
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
        row = self._fetchone(
            "SELECT value FROM kv_store WHERE key = ?;",
            (key,),
        )
        return row[0] if row else None

    def set_kv(self, key: str, value: Any) -> None:
        if isinstance(value, (dict, list)):
            stored_value = json.dumps(value)
        else:
            stored_value = str(value)
        updated_at = int(time.time())
        self._execute(
            """
            INSERT INTO kv_store (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at;
            """,
            (key, stored_value, updated_at),
        )

    def update_watermark(self, symbol: str, timeframe: str, timestamp: int) -> None:
        key = f"last_fetch_{symbol}_{timeframe}"
        self.set_kv(key, int(timestamp))

    def log_signal(self, signal: Dict[str, Any]) -> bool:
        try:
            self._execute(
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
            return True
        except sqlite3.IntegrityError:
            return False

    def is_signal_duplicate(self, signal_hash: str) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM signals WHERE signal_hash = ? LIMIT 1;",
            (signal_hash,),
        )
        return row is not None

    def save_signal(self, signal: Signal) -> None:
        self._execute(
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
                status,
                order_type,
                strategy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                signal.status,
                getattr(signal, "order_type", "LIMIT"),
                getattr(signal, "strategy", None),
            ),
        )

    def get_open_signals(self) -> List[Signal]:
        rows = self._fetchall(
            """
            SELECT
                signal_hash,
                symbol,
                COALESCE(signal_type, type) AS signal_type,
                COALESCE(entry_price, entry) AS entry_price,
                COALESCE(sl_price, sl) AS sl_price,
                COALESCE(tp1_price, tp1) AS tp1_price,
                COALESCE(tp2_price, tp2) AS tp2_price,
                COALESCE(score, 0) AS score,
                COALESCE(reasoning, '') AS reasoning,
                COALESCE(timestamp, created_at, 0) AS timestamp,
                telegram_message_id,
                telegram_chat_id,
                closure_reason,
                status,
                COALESCE(order_type, 'LIMIT') AS order_type,
                strategy,
                COALESCE(mfe_r, 0.0) AS mfe_r
            FROM signals
            WHERE status IN ('PENDING', 'ACTIVE', 'PARTIAL_TP1')
            ORDER BY created_at ASC, id ASC;
            """
        )
        signals: List[Signal] = []
        for row in rows:
            if row[0] is None or row[1] is None:
                continue
            signals.append(
                Signal(
                    signal_hash=str(row[0]),
                    symbol=str(row[1]),
                    signal_type=str(row[2] or ""),
                    entry_price=float(row[3]),
                    sl_price=float(row[4]),
                    tp1_price=float(row[5]),
                    tp2_price=float(row[6]),
                    score=int(row[7] or 0),
                    reasoning=str(row[8] or ""),
                    timestamp=int(row[9] or 0),
                    telegram_message_id=row[10],
                    telegram_chat_id=str(row[11]) if row[11] is not None else None,
                    closure_reason=str(row[12]) if row[12] is not None else None,
                    status=str(row[13] or "PENDING"),
                    order_type=str(row[14] or "LIMIT"),
                    strategy=str(row[15]) if row[15] is not None else None,
                    mfe_r=float(row[16] or 0.0),
                )
            )
        return signals

    def update_signal_status(self, signal_hash: str, new_status: str) -> None:
        self._execute(
            """
            UPDATE signals
            SET status = ?
            WHERE signal_hash = ?;
            """,
            (str(new_status).upper(), signal_hash),
        )

    def update_signal_telegram_metadata(
        self,
        signal_hash: str,
        telegram_message_id: str,
        telegram_chat_id: str,
    ) -> None:
        self._execute(
            """
            UPDATE signals
            SET telegram_message_id = ?, telegram_chat_id = ?
            WHERE signal_hash = ?;
            """,
            (str(telegram_message_id), str(telegram_chat_id), signal_hash),
        )

    def update_signal_message_id(self, signal_hash: str, message_id: int) -> None:
        self._execute(
            """
            UPDATE signals
            SET telegram_message_id = ?
            WHERE signal_hash = ?;
            """,
            (int(message_id), signal_hash),
        )

    def get_signal_message_id(self, signal_hash: str) -> int:
        row = self._fetchone(
            """
            SELECT telegram_message_id
            FROM signals
            WHERE signal_hash = ?
            LIMIT 1;
            """,
            (signal_hash,),
        )
        if row is None or row[0] is None:
            raise KeyError(f"No telegram_message_id found for signal_hash={signal_hash}")
        return int(row[0])

    def update_signal_closure(
        self,
        signal_hash: str,
        closure_reason: str,
        status: str,
    ) -> None:
        self._execute(
            """
            UPDATE signals
            SET closure_reason = ?, status = ?
            WHERE signal_hash = ?;
            """,
            (closure_reason, status, signal_hash),
        )

    def save_zone(self, zone: Dict[str, Any]) -> None:
        created_at = int(zone.get("created_at", int(time.time())))
        self._execute(
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

    def get_active_zones(self, symbol: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT id, symbol, timeframe, type, price_top, price_bottom, status, created_at
            FROM zones
            WHERE symbol = ? AND status IN ('ACTIVE', 'UNMITIGATED')
            ORDER BY created_at DESC;
            """,
            (symbol,),
        )
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

    def get_recent_order_blocks(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT id, symbol, timeframe, type, price_top, price_bottom, status, created_at
            FROM zones
            WHERE symbol = ?
              AND status IN ('ACTIVE', 'UNMITIGATED', 'MITIGATED')
              AND type IN ('OB_BULLISH', 'OB_BEARISH')
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (symbol, limit),
        )
        return [
            {
                "id": row[0],
                "symbol": row[1],
                "timeframe": row[2],
                "type": row[3],
                "price_top": float(row[4]),
                "price_bottom": float(row[5]),
                "status": row[6],
                "created_at": int(row[7]),
            }
            for row in rows
        ]

    def get_recent_unmitigated_fvgs(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT id, symbol, timeframe, type, price_top, price_bottom, status, created_at
            FROM zones
            WHERE symbol = ?
              AND timeframe = ?
              AND status = 'UNMITIGATED'
              AND type IN ('FVG_BULLISH', 'FVG_BEARISH')
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (symbol, timeframe, limit),
        )
        return [
            {
                "id": row[0],
                "symbol": row[1],
                "timeframe": row[2],
                "type": row[3],
                "price_top": float(row[4]),
                "price_bottom": float(row[5]),
                "status": row[6],
                "created_at": int(row[7]),
            }
            for row in rows
        ]

    def get_gold_closes_since(self, cutoff_timestamp: int) -> List[tuple[int, float]]:
        rows = self._fetchall(
            """
            SELECT timestamp, close
            FROM market_data
            WHERE symbol = ? AND timestamp >= ?
            ORDER BY timestamp ASC;
            """,
            ("XAUUSD", cutoff_timestamp),
        )
        output: List[tuple[int, float]] = []
        for row in rows:
            output.append((int(row[0]), float(row[1])))
        return output

    def update_zone_statuses(self, updated_zones: List[Dict[str, Any]]) -> None:
        payload: List[tuple[str, int]] = []
        for zone in updated_zones:
            zone_id = zone.get("id")
            zone_status = zone.get("status")
            if zone_id is None or zone_status is None:
                continue
            payload.append((str(zone_status), int(zone_id)))
        self._executemany(
            """
            UPDATE zones
            SET status = ?
            WHERE id = ?;
            """,
            payload,
        )

    def log_error(self, provider: str, error_code: str, message: str, timestamp: int) -> None:
        self._execute(
            """
            INSERT INTO errors (provider, error_code, message, timestamp)
            VALUES (?, ?, ?, ?);
            """,
            (provider, error_code, message, timestamp),
        )

    def count_signals_since(self, cutoff_timestamp: int) -> int:
        row = self._fetchone(
            """
            SELECT COUNT(*)
            FROM signals
            WHERE COALESCE(timestamp, created_at, 0) >= ?;
            """,
            (int(cutoff_timestamp),),
        )
        if row is None or row[0] is None:
            return 0
        return int(row[0])

    def count_candles_since(self, cutoff_timestamp: int) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) FROM market_data WHERE timestamp >= ?;",
            (int(cutoff_timestamp),),
        )
        if row is None or row[0] is None:
            return 0
        return int(row[0])

    def get_strategy_outcomes(self, strategy: str, limit: int = 30) -> List[str]:
        rows = self._fetchall(
            """
            SELECT status
            FROM signals
            WHERE strategy = ?
              AND status IN ('CLOSED_TP2', 'CLOSED_SL', 'CLOSED_BE')
            ORDER BY id DESC
            LIMIT ?;
            """,
            (str(strategy), int(limit)),
        )
        return [str(row[0]) for row in rows if row and row[0] is not None]

    def get_closed_outcomes_since(self, cutoff_timestamp: int) -> List[tuple]:
        """(strategy, status) for every closed trade since the cutoff."""
        rows = self._fetchall(
            """
            SELECT COALESCE(strategy, 'UNKNOWN'), status
            FROM signals
            WHERE status IN ('CLOSED_TP2', 'CLOSED_SL', 'CLOSED_BE',
                             'CLOSED_TIME', 'CLOSED_STRUCT')
              AND COALESCE(timestamp, created_at, 0) >= ?;
            """,
            (int(cutoff_timestamp),),
        )
        return [
            (str(row[0]), str(row[1]))
            for row in rows
            if row and row[1] is not None
        ]

    def update_signal_excursions(self, signal_hash: str, mfe_r: float, mae_r: float) -> None:
        """Ratchet max favorable/adverse excursion (in R) for open signals."""
        self._execute(
            """
            UPDATE signals
            SET mfe_r = MAX(COALESCE(mfe_r, 0.0), ?),
                mae_r = MAX(COALESCE(mae_r, 0.0), ?)
            WHERE signal_hash = ?;
            """,
            (round(float(mfe_r), 4), round(float(mae_r), 4), signal_hash),
        )

    def prune_market_data(self, retention_days: int) -> None:
        # Anchor to the newest stored candle (not wall clock) so backfilled or
        # historical datasets are never wiped wholesale.
        row = self._fetchone("SELECT MAX(timestamp) FROM market_data;")
        if row is None or row[0] is None:
            return
        cutoff = int(row[0]) - int(retention_days) * 86400
        self._execute(
            "DELETE FROM market_data WHERE timestamp < ?;",
            (cutoff,),
        )
