from unittest.mock import MagicMock, patch

from src.alerting.telegram_client import TelegramClient
from src.analysis.filters import PermissionEngine
from src.analysis.signal_factory import SignalFactory
from src.core.orchestrator import PulseOrchestrator
from src.domain.candle import Candle


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


def test_cot_overcrowded_gate() -> None:
    engine = PermissionEngine()
    permitted, message = engine.is_trade_permitted(
        setup_dict={"trade_direction": "LONG"},
        macro_context={
            "macro_cot_state": "OVERCROWDED_LONG",
            "macro_consensus_state": "NEUTRAL",
            "macro_long_bias_multiplier": 1.0,
        },
    )

    assert permitted is False
    assert "COT" in message


def test_sovereign_floor_gate() -> None:
    engine = PermissionEngine()
    permitted, message = engine.is_trade_permitted(
        setup_dict={"trade_direction": "SHORT"},
        macro_context={
            "macro_cot_state": "NEUTRAL",
            "macro_consensus_state": "NEUTRAL",
            "macro_long_bias_multiplier": 1.25,
        },
    )

    assert permitted is False
    assert "Sovereign" in message


def test_permitted_trade_and_fallback() -> None:
    engine = PermissionEngine()
    permitted, message = engine.is_trade_permitted(
        setup_dict={"trade_direction": "LONG"},
        macro_context={
            "macro_cot_state": None,
            "macro_consensus_state": None,
            "macro_long_bias_multiplier": None,
        },
    )

    assert permitted is True
    assert message == "Permitted"


def test_orchestrator_integration_blocked_setup() -> None:
    candles = _build_recent_candles()
    zone = {
        "id": 1,
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "type": "OB_BULLISH",
        "price_top": 2000.0,
        "price_bottom": 1990.0,
        "status": "ACTIVE",
    }

    repo_mock = MagicMock()
    repo_mock.connection = MagicMock()
    repo_mock.get_recent_candles.return_value = candles

    def get_kv(key: str) -> str | None:
        if key == "macro_cot_state":
            return "OVERCROWDED_LONG"
        if key == "macro_consensus_state":
            return "NEUTRAL"
        if key == "macro_long_bias_multiplier":
            return "1.0"
        if key == "macro_bias_state":
            return "BIAS_LONG"
        if key == "current_structure_state":
            return "BULLISH"
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

    with patch.object(SignalFactory, "build_signal", autospec=True) as build_signal_mock:
        with patch.object(TelegramClient, "send_message", autospec=True) as send_message_mock:
            orchestrator.run()
            build_signal_mock.assert_not_called()
            send_message_mock.assert_not_called()


def main() -> None:
    test_cot_overcrowded_gate()
    test_sovereign_floor_gate()
    test_permitted_trade_and_fallback()
    test_orchestrator_integration_blocked_setup()
    print("Sprint 28 Permission Filters Verified")


if __name__ == "__main__":
    main()
