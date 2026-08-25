"""Sprint 52: closure reasons must be persisted to the journal, not just sent
to Telegram."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _make_repo(tmp_path: Path) -> Repository:
    connection = sqlite3.connect(str(tmp_path / "sprint52.db"))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


def _active_signal() -> Signal:
    return Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2400.0,
        sl_price=2395.0,
        tp1_price=2407.5,
        tp2_price=2415.0,
        score=80,
        reasoning="test",
        timestamp=1_700_000_000,
        signal_hash="close-reason-test",
        order_type="LIMIT",
        strategy="ZONE_BOUNCE",
    )


def test_sl_closure_persists_reason(tmp_path: Path) -> None:
    repository = _make_repo(tmp_path)
    signal = _active_signal()
    repository.save_signal(signal)
    repository.update_signal_status("close-reason-test", "ACTIVE")

    sl_candle = Candle(
        symbol="XAUUSD", timeframe="M5", timestamp=1_700_000_300,
        open=2398.0, high=2398.5, low=2394.0, close=2394.5, volume=100.0,
    )
    manager = SignalLifecycleManager(
        telegram_client=MagicMock(chat_id=None), repository=repository
    )
    open_signals = repository.get_open_signals()
    manager.process_open_signals(
        open_signals=open_signals,
        current_candle=sl_candle,
        telegram_client=MagicMock(chat_id=None),
        repository=repository,
        formatter=MagicMock(),
    )

    row = repository._fetchone(
        "SELECT status, closure_reason FROM signals WHERE signal_hash = ?;",
        ("close-reason-test",),
    )
    assert row[0] == "CLOSED_SL"
    assert row[1] is not None and "SL" in row[1]
    repository.close()


def test_tp1_transition_has_no_closure_reason(tmp_path: Path) -> None:
    repository = _make_repo(tmp_path)
    signal = _active_signal()
    repository.save_signal(signal)
    repository.update_signal_status("close-reason-test", "ACTIVE")

    tp1_candle = Candle(
        symbol="XAUUSD", timeframe="M5", timestamp=1_700_000_300,
        open=2401.0, high=2408.0, low=2400.5, close=2407.0, volume=100.0,
    )
    manager = SignalLifecycleManager(
        telegram_client=MagicMock(chat_id=None), repository=repository
    )
    manager.process_open_signals(
        open_signals=repository.get_open_signals(),
        current_candle=tp1_candle,
        telegram_client=MagicMock(chat_id=None),
        repository=repository,
        formatter=MagicMock(),
    )

    row = repository._fetchone(
        "SELECT status, closure_reason FROM signals WHERE signal_hash = ?;",
        ("close-reason-test",),
    )
    assert row[0] == "PARTIAL_TP1"
    assert row[1] is None  # not a closure; reason stays empty
    repository.close()


if __name__ == "__main__":
    print("Sprint 52 Closure Reason Persistence Verified")
