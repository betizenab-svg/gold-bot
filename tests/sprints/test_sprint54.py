"""Sprint 54: a trade update that Telegram fails to deliver must not stop the
bot from checking the other open trades and the remaining new candles."""

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock

from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.alerting.telegram_client import TelegramAPIError
from src.core.orchestrator import PulseOrchestrator
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer

T0 = 1_700_000_000


def _signal(signal_hash: str, sl: float, tp1: float, tp2: float) -> Signal:
    return Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2400.0,
        sl_price=sl,
        tp1_price=tp1,
        tp2_price=tp2,
        score=80,
        reasoning="test",
        timestamp=T0,
        signal_hash=signal_hash,
        order_type="LIMIT",
        strategy="ZONE_BOUNCE",
    )


def _candle(offset: int, high: float, low: float, close: float) -> Candle:
    return Candle("XAUUSD", "M1", T0 + offset, 2400.0, high, low, close, 100.0)


def _repository_with_two_open_trades(db_path: Path, b_tp1: float) -> Repository:
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    for index, signal in enumerate(
        (_signal("a", 2395.0, 2407.5, 2415.0), _signal("b", 2390.0, b_tp1, 2440.0))
    ):
        repository.save_signal(signal)
        repository.update_signal_status(signal.signal_hash, "ACTIVE")
        repository.update_signal_message_id(signal.signal_hash, 100 + index)
    return repository


def test_failed_update_does_not_skip_other_trades(tmp_path: Path) -> None:
    repository = _repository_with_two_open_trades(tmp_path / "sprint54.db", b_tp1=2405.0)
    telegram = MagicMock()
    telegram.send_message.side_effect = [TelegramAPIError("timed out"), 11, 12]
    manager = SignalLifecycleManager(telegram_client=telegram, repository=repository)

    # Both trades hit TP1 on this candle; the first alert fails to send.
    candle = _candle(60, high=2408.0, low=2399.0, close=2407.0)
    undelivered = manager.process_open_signals(
        repository.get_open_signals(), candle, telegram, repository, manager.formatter
    )

    assert undelivered == 1
    statuses = dict(repository._fetchall("SELECT signal_hash, status FROM signals;"))
    assert statuses == {"a": "PARTIAL_TP1", "b": "PARTIAL_TP1"}
    assert telegram.send_message.call_count == 3
    repository.close()


def test_pulse_keeps_walking_candles_after_failed_update(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint54_pulse.db"
    repository = _repository_with_two_open_trades(db_path, b_tp1=2420.0)
    candles = [
        _candle(60, high=2408.0, low=2399.0, close=2407.0),  # trade a: TP1 (alert fails)
        _candle(120, high=2401.0, low=2389.0, close=2390.0),  # a: breakeven, b: stop loss
    ]

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            return candles

    telegram = MagicMock()
    telegram.send_message.side_effect = [TelegramAPIError("timed out"), 11, 12, 13, 14]
    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repository,
        client_factory=lambda _repo: StubClient(),
        memory_profiler=MagicMock(),
        structured_logger=MagicMock(),
        lifecycle_manager_factory=lambda repo: SignalLifecycleManager(
            telegram_client=telegram, repository=repo
        ),
    )
    orchestrator._run_macro_regime_check = MagicMock()

    orchestrator.run()

    with closing(sqlite3.connect(str(db_path))) as check:
        statuses = dict(check.execute("SELECT signal_hash, status FROM signals;").fetchall())
        error_streak = check.execute(
            "SELECT value FROM kv_store WHERE key = 'consecutive_pulse_errors';"
        ).fetchone()
    assert statuses == {"a": "CLOSED_BE", "b": "CLOSED_SL"}
    assert telegram.send_message.call_count == 5
    # The undelivered update still counts as a pulse error (health alarm/telemetry).
    assert error_streak == ("1",)
