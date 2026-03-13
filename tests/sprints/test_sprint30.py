from unittest.mock import MagicMock, patch

from src.alerting.formatter import SignalFormatter
from src.alerting.lifecycle_manager import LifecycleManager
from src.alerting.telegram_client import TelegramClient
from src.core.orchestrator import PulseOrchestrator
from src.domain.candle import Candle
from src.domain.signal import Signal


def _make_candle(
    timestamp: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
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


def _build_recent_candles() -> list[Candle]:
    base_ts = 1_700_000_000
    candles: list[Candle] = []
    for index in range(15):
        candles.append(
            _make_candle(
                timestamp=base_ts + (index * 60),
                open_price=2000.0,
                high=2005.0,
                low=1995.0,
                close=2001.0,
                volume=100.0,
            )
        )
    return candles


def test_html_formatting() -> None:
    formatter = SignalFormatter()
    signal = Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2000.50,
        sl_price=1990.00,
        tp1_price=2010.00,
        tp2_price=2020.00,
        score=80,
        reasoning="Test setup",
        timestamp=1_700_000_000,
        signal_hash="signal-30",
    )

    formatted = formatter.format_initial_signal(signal)
    assert "🚨 <b>Signal Alert</b>" in formatted
    assert "🟡 <b>Status:</b> market execution/pending order" in formatted
    assert "entry @ <code>2000.50</code>" in formatted


def test_lifecycle_formatting() -> None:
    formatter = SignalFormatter()
    alert_message, explanation_message = formatter.format_lifecycle_update(
        "TP1_SMASH",
        "Hit resistance at 2010.00",
    )
    assert "TP 1 Smashed" in alert_message
    assert "<code>2010.00</code>" in explanation_message


def test_orchestrator_threading_logic() -> None:
    candles = _build_recent_candles()
    zone = {
        "id": 1,
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "type": "OB_BULLISH",
        "price_top": 2000.00,
        "price_bottom": 1990.00,
        "status": "ACTIVE",
    }

    repo_mock = MagicMock()
    repo_mock.connection = MagicMock()
    repo_mock.get_recent_candles.return_value = candles
    repo_mock.is_signal_duplicate.return_value = False

    def get_kv(key: str) -> str | None:
        if key == "macro_bias_state":
            return "BIAS_LONG"
        if key == "current_structure_state":
            return "BULLISH"
        if key == "latest_liquidity_sweep":
            return '{"type":"LIQUIDITY_SWEEP_LONG","timestamp":1700000840}'
        if key in {"macro_cot_state", "macro_consensus_state"}:
            return None
        if key == "macro_long_bias_multiplier":
            return None
        return None

    repo_mock.get_kv.side_effect = get_kv

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            assert symbol == "XAUUSD"
            assert timeframe == "M1"
            return candles

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repo_mock,
        client_factory=lambda repo: StubClient(),
        memory_profiler=MagicMock(),
    )
    orchestrator._run_macro_regime_check = MagicMock()
    orchestrator._evaluate_zone_lifecycle = MagicMock()
    orchestrator._persist_latest_fractals = MagicMock()
    orchestrator._evaluate_liquidity_sweep = MagicMock()
    orchestrator._evaluate_market_structure = MagicMock(return_value=None)
    orchestrator._scan_for_fvg_zones = MagicMock(return_value=[])
    orchestrator._scan_for_order_blocks = MagicMock()
    orchestrator._detect_trade_setup = MagicMock(
        return_value={
            "trade_direction": "LONG",
            "zone": zone,
            "strategy": "PIN_BAR_REJECTION",
        }
    )

    with patch.object(TelegramClient, "send_message", autospec=True) as send_message_mock:
        send_message_mock.side_effect = [9999, 10000]
        orchestrator.run()

    saved_signal = repo_mock.save_signal.call_args.args[0]
    repo_mock.update_signal_message_id.assert_called_once_with(
        signal_hash=saved_signal.signal_hash,
        message_id=9999,
    )
    second_call = send_message_mock.call_args_list[1]
    assert second_call.kwargs["reply_to_message_id"] == 9999


def test_lifecycle_manager_uses_stored_message_id() -> None:
    telegram_client = MagicMock()
    telegram_client.chat_id = "chat-123"
    telegram_client.send_message.side_effect = [20001, 20002]
    repository = MagicMock()
    repository.get_signal_message_id.return_value = 9999
    lifecycle_manager = LifecycleManager(
        telegram_client=telegram_client,
        repository=repository,
    )

    signal = Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2000.50,
        sl_price=1990.00,
        tp1_price=2010.00,
        tp2_price=2020.00,
        score=80,
        reasoning="Test setup",
        timestamp=1_700_000_000,
        signal_hash="signal-30",
        telegram_chat_id="chat-123",
    )

    lifecycle_manager.send_lifecycle_update(
        signal,
        "TP1_SMASH",
        "Hit resistance at 2010.00",
    )

    repository.get_signal_message_id.assert_called_once_with("signal-30")
    first_call = telegram_client.send_message.call_args_list[0]
    second_call = telegram_client.send_message.call_args_list[1]
    assert first_call.kwargs["reply_to_message_id"] == 9999
    assert second_call.kwargs["reply_to_message_id"] == 9999


def main() -> None:
    test_html_formatting()
    test_lifecycle_formatting()
    test_orchestrator_threading_logic()
    test_lifecycle_manager_uses_stored_message_id()
    print("Sprint 30 Smart Formatting & Thread Orchestration Verified")


if __name__ == "__main__":
    main()
