import sqlite3
from unittest.mock import MagicMock

from src.alerting.formatter import SignalFormatter
from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.core.orchestrator import PulseOrchestrator
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _make_candle(
    timestamp: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _make_signal(
    signal_hash: str,
    signal_type: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    status: str,
    telegram_message_id: int | None = 4455,
) -> Signal:
    return Signal(
        symbol="XAUUSD",
        signal_type=signal_type,
        entry_price=entry,
        sl_price=sl,
        tp1_price=tp1,
        tp2_price=tp2,
        score=88,
        reasoning="Lifecycle test signal.",
        timestamp=1_700_000_000,
        signal_hash=signal_hash,
        telegram_message_id=telegram_message_id,
        telegram_chat_id="chat-123",
        status=status,
    )


def test_long_evaluation() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _make_signal(
        signal_hash="long-active",
        signal_type="LONG",
        entry=2000.0,
        sl=1990.0,
        tp1=2010.0,
        tp2=2020.0,
        status="ACTIVE",
    )

    tp1_candle = _make_candle(1_700_000_060, 2008.0, 2012.0, 2005.0, 2010.5)
    assert manager.evaluate_signal(signal, tp1_candle) == "TP1_SMASH"

    sl_candle = _make_candle(1_700_000_120, 1996.0, 2005.0, 1988.0, 1991.0)
    assert manager.evaluate_signal(signal, sl_candle) == "SL_HIT"


def test_short_evaluation() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _make_signal(
        signal_hash="short-pending",
        signal_type="SHORT",
        entry=2000.0,
        sl=2010.0,
        tp1=1990.0,
        tp2=1980.0,
        status="PENDING",
    )

    activation_candle = _make_candle(1_700_000_180, 1998.0, 2005.0, 1995.0, 1997.0)
    assert manager.evaluate_signal(signal, activation_candle) == "ACTIVATED"


def test_repository_open_signal_filter() -> None:
    connection = sqlite3.connect(":memory:")
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)

    pending_signal = _make_signal("pending", "LONG", 2000.0, 1990.0, 2010.0, 2020.0, "PENDING")
    active_signal = _make_signal("active", "LONG", 2000.0, 1990.0, 2010.0, 2020.0, "ACTIVE")
    partial_signal = _make_signal(
        "partial", "SHORT", 2000.0, 2010.0, 1990.0, 1980.0, "PARTIAL_TP1"
    )
    closed_signal = _make_signal(
        "closed", "LONG", 2000.0, 1990.0, 2010.0, 2020.0, "CLOSED_SL"
    )

    repository.save_signal(pending_signal)
    repository.save_signal(active_signal)
    repository.save_signal(partial_signal)
    repository.save_signal(closed_signal)

    open_hashes = {signal.signal_hash for signal in repository.get_open_signals()}
    assert open_hashes == {"pending", "active", "partial"}

    repository.update_signal_status("active", "CLOSED_TP2")
    open_hashes_after_close = {signal.signal_hash for signal in repository.get_open_signals()}
    assert open_hashes_after_close == {"pending", "partial"}


def test_processing_and_threading_mocked() -> None:
    telegram_client = MagicMock()
    repository = MagicMock()
    repository.get_signal_message_id.return_value = 4455
    formatter = MagicMock(spec=SignalFormatter)
    formatter.format_lifecycle_update.return_value = ("Alert", "Reason")

    manager = SignalLifecycleManager(
        telegram_client=telegram_client,
        repository=repository,
        formatter=formatter,
    )
    signal = _make_signal(
        signal_hash="tp1-long",
        signal_type="LONG",
        entry=2000.0,
        sl=1990.0,
        tp1=2010.0,
        tp2=2020.0,
        status="ACTIVE",
    )
    tp1_candle = _make_candle(1_700_000_060, 2008.0, 2012.0, 2005.0, 2010.5)

    manager.process_open_signals([signal], tp1_candle, telegram_client, repository, formatter)

    repository.update_signal_status.assert_called_once_with("tp1-long", "PARTIAL_TP1")
    assert telegram_client.send_message.call_count == 2
    for call in telegram_client.send_message.call_args_list:
        assert call.kwargs["reply_to_message_id"] == 4455


def test_orchestrator_runs_lifecycle_monitor_before_setup_scan() -> None:
    candles = [
        _make_candle(1_700_000_000 + (index * 60), 2000.0, 2005.0, 1995.0, 2001.0)
        for index in range(15)
    ]
    open_signal = _make_signal(
        signal_hash="open-long",
        signal_type="LONG",
        entry=2000.0,
        sl=1990.0,
        tp1=2010.0,
        tp2=2020.0,
        status="ACTIVE",
    )

    repo_mock = MagicMock()
    repo_mock.connection = MagicMock()
    repo_mock.get_recent_candles.return_value = candles
    repo_mock.get_open_signals.return_value = [open_signal]

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            assert symbol == "XAUUSD"
            assert timeframe == "M1"
            return candles

    lifecycle_manager = MagicMock()
    lifecycle_manager.telegram_client = MagicMock()
    lifecycle_manager.formatter = MagicMock()
    call_order: list[str] = []
    lifecycle_manager.process_open_signals.side_effect = (
        lambda *args, **kwargs: call_order.append("lifecycle")
    )

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repo_mock,
        client_factory=lambda repo: StubClient(),
        memory_profiler=MagicMock(),
        lifecycle_manager_factory=lambda repo: lifecycle_manager,
    )
    orchestrator._run_macro_regime_check = MagicMock()
    orchestrator._evaluate_zone_lifecycle = MagicMock()
    orchestrator._persist_latest_fractals = MagicMock()
    orchestrator._evaluate_liquidity_sweep = MagicMock()
    orchestrator._evaluate_market_structure = MagicMock(return_value=None)
    orchestrator._scan_for_fvg_zones = MagicMock(return_value=[])
    orchestrator._scan_for_order_blocks = MagicMock()
    orchestrator._detect_trade_setup = MagicMock(
        side_effect=lambda *args, **kwargs: call_order.append("setup") or None
    )

    orchestrator.run()

    assert call_order == ["lifecycle", "setup"]


def main() -> None:
    test_long_evaluation()
    test_short_evaluation()
    test_repository_open_signal_filter()
    test_processing_and_threading_mocked()
    test_orchestrator_runs_lifecycle_monitor_before_setup_scan()
    print("Sprint 32 Signal Lifecycle Manager Verified")


if __name__ == "__main__":
    main()
