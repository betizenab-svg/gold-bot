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
        self._file_connection: Optional[sqlite3.Connection] = None

        db_path = self._extract_db_path(connection)
        if db_path in {"", ":memory:"}:
            self.connection = connection
            self._shared_connection_mode = True
            self._configure_connection(connection)
            return

        self._db_path = db_path
        self._configure_connection(connection)
        # Reconnecting per query cost ~30 connects + PRAGMA rounds per pulse.
        self._file_connection = connection

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

        if self._file_connection is not None:
            return self._file_connection

        if not self._db_path:
            raise RuntimeError("Repository database path is not configured")

        # File-backed repositories stay usable after close(): reopen lazily.
        connection = sqlite3.connect(self._db_path)
        self._configure_connection(connection)
        self._file_connection = connection
        return connection

    def _fetchall(self, query: str, params: Iterable[Any] = ()) -> List[tuple[Any, ...]]:
        connection = self._open_connection()
        with closing(connection.cursor()) as cursor:
            cursor.execute(query, tuple(params))
            return cursor.fetchall()

    def _fetchone(self, query: str, params: Iterable[Any] = ()) -> Optional[tuple[Any, ...]]:
        connection = self._open_connection()
        with closing(connection.cursor()) as cursor:
            cursor.execute(query, tuple(params))
            return cursor.fetchone()

    def _execute(self, query: str, params: Iterable[Any] = ()) -> None:
        connection = self._open_connection()
        with connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(query, tuple(params))

    def _executemany(self, query: str, payload: List[tuple[Any, ...]]) -> None:
        if not payload:
            return

        connection = self._open_connection()
        if self._shared_connection_mode:
            connection.executemany(query, payload)
            connection.commit()
            return

        with connection:
            with closing(connection.cursor()) as cursor:
                cursor.executemany(query, payload)

    def close(self) -> None:
        if self._shared_connection_mode and self.connection is not None:
            self.connection.close()
            self.connection = None
        if self._file_connection is not None:
            self._file_connection.close()
            self._file_connection = None

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
                strategy,
                price_source,
                trial,
                code_version,
                risk_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                getattr(signal, "price_source", None),
                1 if getattr(signal, "trial", False) else 0,
                getattr(signal, "code_version", None),
                getattr(signal, "risk_pct", None),
            ),
        )

    def get_signal_id(self, signal_hash: str) -> Optional[int]:
        row = self._fetchone(
            "SELECT id FROM signals WHERE signal_hash = ? LIMIT 1;", (signal_hash,)
        )
        return int(row[0]) if row and row[0] is not None else None

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
                COALESCE(mfe_r, 0.0) AS mfe_r,
                id,
                price_source,
                COALESCE(trial, 0) AS trial
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
                    id=int(row[17]) if row[17] is not None else None,
                    price_source=str(row[18]) if row[18] is not None else None,
                    trial=bool(row[19]),
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
        realized_r: Optional[float] = None,
        closed_at: Optional[int] = None,
    ) -> None:
        self._execute(
            """
            UPDATE signals
            SET closure_reason = ?, status = ?,
                realized_r = COALESCE(?, realized_r),
                closed_at = COALESCE(?, closed_at)
            WHERE signal_hash = ?;
            """,
            (
                closure_reason,
                status,
                None if realized_r is None else round(float(realized_r), 4),
                None if closed_at is None else int(closed_at),
                signal_hash,
            ),
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
        """Public signals since the cutoff (trial signals use no public budget)."""
        row = self._fetchone(
            """
            SELECT COUNT(*)
            FROM signals
            WHERE COALESCE(timestamp, created_at, 0) >= ? AND COALESCE(trial, 0) = 0;
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

    def get_strategy_realized_r(self, strategy: str, limit: int = 30) -> List[float]:
        """Exact R of the strategy's latest closed public trades, newest first."""
        from src.analysis.outcomes import row_r

        rows = self._fetchall(
            """
            SELECT status, realized_r
            FROM signals
            WHERE strategy = ? AND status LIKE 'CLOSED%' AND COALESCE(trial, 0) = 0
            ORDER BY id DESC
            LIMIT ?;
            """,
            (str(strategy), int(limit)),
        )
        values: List[float] = []
        for status, realized in rows:
            value = row_r(status, realized)
            if value is not None:
                values.append(float(value))
        return values

    def get_closed_results_since(self, cutoff_timestamp: int) -> List[tuple]:
        """(strategy, symbol, r) for every closed public trade since the cutoff."""
        from src.analysis.outcomes import row_r

        rows = self._fetchall(
            """
            SELECT COALESCE(strategy, 'UNKNOWN'), symbol, status, realized_r
            FROM signals
            WHERE status LIKE 'CLOSED%' AND COALESCE(trial, 0) = 0
              AND COALESCE(closed_at, timestamp, created_at, 0) >= ?
            ORDER BY COALESCE(closed_at, timestamp, created_at, 0) ASC, id ASC;
            """,
            (int(cutoff_timestamp),),
        )
        output: List[tuple] = []
        for strategy, symbol, status, realized in rows:
            value = row_r(status, realized)
            if value is not None:
                output.append((str(strategy), str(symbol or "XAUUSD"), float(value)))
        return output

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

    def log_setup(
        self,
        symbol: str,
        strategy: str,
        direction: str,
        order_type: str,
        score: int,
        classification: str,
        vetoes: str,
        timestamp: int,
        levels: Optional[tuple[float, float, float, float]] = None,
    ) -> None:
        """Detection-funnel telemetry: every scored setup, published or not.
        With levels, the idea is also followed to see what it would have done."""
        entry = sl = tp1 = tp2 = None
        shadow_status = None
        if levels is not None:
            entry, sl, tp1, tp2 = (float(value) for value in levels)
            shadow_status = "PENDING"
        self._execute(
            """
            INSERT INTO setup_log
                (symbol, strategy, direction, order_type, score,
                 classification, vetoes, timestamp,
                 entry_price, sl_price, tp1_price, tp2_price, shadow_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                str(symbol), str(strategy), str(direction), str(order_type),
                int(score), str(classification), str(vetoes), int(timestamp),
                entry, sl, tp1, tp2, shadow_status,
            ),
        )

    def get_open_shadow_setups(self, symbol: str) -> List[dict]:
        rows = self._fetchall(
            """
            SELECT id, symbol, strategy, direction, order_type, timestamp,
                   entry_price, sl_price, tp1_price, tp2_price,
                   shadow_status, COALESCE(shadow_mfe_r, 0.0)
            FROM setup_log
            WHERE symbol = ? AND shadow_status IN ('PENDING', 'ACTIVE', 'PARTIAL_TP1')
            ORDER BY id ASC;
            """,
            (str(symbol),),
        )
        return [
            {
                "id": int(row[0]),
                "symbol": row[1],
                "strategy": row[2],
                "signal_type": row[3],
                "order_type": row[4] or "LIMIT",
                "timestamp": int(row[5] or 0),
                "entry_price": float(row[6]),
                "sl_price": float(row[7]),
                "tp1_price": float(row[8]),
                "tp2_price": float(row[9]),
                "status": row[10],
                "mfe_r": float(row[11] or 0.0),
            }
            for row in rows
            if row[6] is not None and row[7] is not None
        ]

    def update_shadow_setup(
        self,
        row_id: int,
        status: str,
        mfe_r: float,
        realized_r: Optional[float] = None,
        closed_at: Optional[int] = None,
    ) -> None:
        self._execute(
            """
            UPDATE setup_log
            SET shadow_status = ?, shadow_mfe_r = ?, shadow_r = ?, shadow_closed_at = ?
            WHERE id = ?;
            """,
            (
                str(status),
                round(float(mfe_r), 4),
                None if realized_r is None else round(float(realized_r), 4),
                closed_at,
                int(row_id),
            ),
        )

    # --- Telegram outbox: nothing is lost when Telegram is down -------------
    def outbox_add(
        self,
        chat_id: str,
        text: str,
        kind: str = "message",
        reply_to_message_id: Optional[int] = None,
        signal_hash: Optional[str] = None,
        status: str = "PENDING",
    ) -> int:
        connection = self._open_connection()
        with connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    """
                    INSERT INTO outbox (chat_id, kind, text, reply_to_message_id,
                                        signal_hash, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        str(chat_id), str(kind), str(text),
                        reply_to_message_id, signal_hash, str(status), int(time.time()),
                    ),
                )
                return int(cursor.lastrowid or 0)

    def outbox_mark(
        self,
        row_id: int,
        status: str,
        message_id: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        self._execute(
            """
            UPDATE outbox
            SET status = ?,
                attempts = attempts + CASE WHEN ? IN ('SENT', 'PENDING', 'FAILED') THEN 1 ELSE 0 END,
                message_id = COALESCE(?, message_id),
                last_error = COALESCE(?, last_error),
                sent_at = CASE WHEN ? = 'SENT' THEN ? ELSE sent_at END
            WHERE id = ?;
            """,
            (
                str(status), str(status), message_id,
                None if error is None else str(error)[:500],
                str(status), int(time.time()), int(row_id),
            ),
        )

    def outbox_rows(self, statuses: Iterable[str], limit: int = 50) -> List[dict]:
        wanted = [str(status) for status in statuses]
        if not wanted:
            return []
        placeholders = ",".join("?" for _ in wanted)
        rows = self._fetchall(
            f"""
            SELECT id, chat_id, kind, text, reply_to_message_id, signal_hash,
                   status, attempts, created_at
            FROM outbox WHERE status IN ({placeholders})
            ORDER BY id ASC LIMIT ?;
            """,
            (*wanted, int(limit)),
        )
        return [
            {
                "id": int(row[0]), "chat_id": row[1], "kind": row[2], "text": row[3],
                "reply_to_message_id": row[4], "signal_hash": row[5],
                "status": row[6], "attempts": int(row[7] or 0), "created_at": int(row[8] or 0),
            }
            for row in rows
        ]

    def prune_outbox(self, older_than_seconds: int = 14 * 86400) -> None:
        self._execute(
            "DELETE FROM outbox WHERE status IN ('SENT', 'FAILED', 'UNCERTAIN') AND created_at < ?;",
            (int(time.time()) - int(older_than_seconds),),
        )

    # --- Feed health and run history --------------------------------------
    def record_feed_health(
        self, symbol: str, source: str, lag_seconds: Optional[int], ok: bool, timestamp: int
    ) -> None:
        self._execute(
            """
            INSERT INTO feed_health (symbol, source, lag_seconds, ok, timestamp)
            VALUES (?, ?, ?, ?, ?);
            """,
            (str(symbol), str(source), lag_seconds, 1 if ok else 0, int(timestamp)),
        )

    def record_pulse_run(
        self, timestamp: int, duration_ms: int, signals: int, errors: int, detail: str
    ) -> None:
        self._execute(
            """
            INSERT INTO pulse_runs (timestamp, duration_ms, signals, errors, detail)
            VALUES (?, ?, ?, ?, ?);
            """,
            (int(timestamp), int(duration_ms), int(signals), int(errors), str(detail)),
        )

    def prune_health_tables(self, retention_days: int = 30) -> None:
        cutoff = int(time.time()) - int(retention_days) * 86400
        self._execute("DELETE FROM feed_health WHERE timestamp < ?;", (cutoff,))
        self._execute("DELETE FROM pulse_runs WHERE timestamp < ?;", (cutoff,))

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[tuple[Any, ...]]:
        """Read-only helper for reports."""
        return self._fetchall(sql, params)

    def purge_symbol_price_history(self, symbol: str) -> None:
        """Drop a market's stored candles, zones and still-open shadow ideas
        (used when its price feed changes and old levels no longer apply)."""
        name = str(symbol).upper()
        self._execute("DELETE FROM market_data WHERE symbol = ?;", (name,))
        self._execute("DELETE FROM zones WHERE symbol = ?;", (name,))
        self._execute(
            "UPDATE setup_log SET shadow_status = 'VOID' "
            "WHERE symbol = ? AND shadow_status IN ('PENDING', 'ACTIVE', 'PARTIAL_TP1');",
            (name,),
        )

    def delete_kv(self, key: str) -> None:
        self._execute("DELETE FROM kv_store WHERE key = ?;", (key,))

    def get_recent_setups(self, limit: int = 20) -> List[dict]:
        rows = self._fetchall(
            """
            SELECT symbol, strategy, direction, order_type, score,
                   classification, vetoes, timestamp
            FROM setup_log ORDER BY id DESC LIMIT ?;
            """,
            (int(limit),),
        )
        return [
            {
                "symbol": row[0], "strategy": row[1], "direction": row[2],
                "order_type": row[3], "score": row[4],
                "classification": row[5], "vetoes": row[6], "timestamp": row[7],
            }
            for row in rows
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

    SLOW_TIMEFRAMES = ("H1", "H4", "D")

    def prune_market_data(self, retention_days: int, slow_retention_days: Optional[int] = None) -> None:
        # Anchor to the newest stored candle (not wall clock) so backfilled or
        # historical datasets are never wiped wholesale.
        row = self._fetchone("SELECT MAX(timestamp) FROM market_data;")
        if row is None or row[0] is None:
            return
        newest = int(row[0])
        cutoff = newest - int(retention_days) * 86400
        if slow_retention_days is None:
            self._execute(
                "DELETE FROM market_data WHERE timestamp < ?;",
                (cutoff,),
            )
            return
        marks = ",".join("?" for _ in self.SLOW_TIMEFRAMES)
        self._execute(
            f"DELETE FROM market_data WHERE timestamp < ? AND timeframe NOT IN ({marks});",
            (cutoff, *self.SLOW_TIMEFRAMES),
        )
        self._execute(
            f"DELETE FROM market_data WHERE timestamp < ? AND timeframe IN ({marks});",
            (newest - int(slow_retention_days) * 86400, *self.SLOW_TIMEFRAMES),
        )

    def replace_candles(self, symbol: str, timeframe: str, candles: Iterable[Candle]) -> None:
        """Swap one chart's stored candles for a longer history from the same feed."""
        self._execute(
            "DELETE FROM market_data WHERE symbol = ? AND timeframe = ?;",
            (str(symbol).upper(), str(timeframe).upper()),
        )
        self.save_candles(candles)
