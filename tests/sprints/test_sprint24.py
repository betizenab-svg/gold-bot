from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

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
                base_ts + (index * 60),
                100.0,
                104.0,
                98.0,
                101.0,
                100.0,
            )
        )
    return candles


def test_parameter_math_long() -> None:
    factory = SignalFactory()
    entry, sl, tp1, tp2 = factory.calculate_parameters(
        trade_direction="LONG",
        zone_dict={"id": 1, "price_top": 2000.00, "price_bottom": 1990.00},
        atr=4.00,
    )

    # SL = zone bottom - 1.5*ATR; TP1/TP2 = 1.5R/3R from the widened stop.
    assert entry == 2000.00
    assert sl == 1984.00
    assert tp1 == 2024.00
    assert tp2 == 2048.00


def test_parameter_math_short() -> None:
    factory = SignalFactory()
    entry, sl, tp1, tp2 = factory.calculate_parameters(
        trade_direction="SHORT",
        zone_dict={"id": 2, "price_top": 2020.00, "price_bottom": 2010.00},
        atr=6.00,
    )

    assert entry == 2010.00
    assert sl == 2029.00
    assert tp1 == 1981.50
    assert tp2 == 1953.00


def test_hash_deduplication() -> None:
    factory = SignalFactory()
    zone = {
        "id": 1,
        "type": "OB_BULLISH",
        "status": "ACTIVE",
        "price_top": 2000.00,
        "price_bottom": 1990.00,
    }

    signal_one = factory.build_signal(
        symbol="XAUUSD",
        trade_direction="LONG",
        zone_dict=zone,
        atr=4.00,
        score=85,
        timestamp=1_700_000_000,
    )
    signal_two = factory.build_signal(
        symbol="XAUUSD",
        trade_direction="LONG",
        zone_dict=zone,
        atr=4.00,
        score=85,
        timestamp=1_700_000_060,
    )

    assert signal_one.signal_hash == signal_two.signal_hash


def test_orchestrator_blocks_duplicate_signal() -> None:
    candles = _build_recent_candles()
    current_candle = candles[-1]
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
    repo_mock.is_signal_duplicate.return_value = True

    def get_kv(key: str) -> str | None:
        if key == "macro_bias_state":
            return None
        if key == "global_macro_bias":
            return "BIAS_LONG"
        if key == "current_structure_state":
            return "BULLISH"
        if key == "latest_liquidity_sweep":
            return '{"type":"LIQUIDITY_SWEEP_LONG","timestamp":1700000840}'
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
        }
    )

    orchestrator.run()

    repo_mock.is_signal_duplicate.assert_called_once()
    repo_mock.save_signal.assert_not_called()


def main() -> None:
    test_parameter_math_long()
    test_parameter_math_short()
    test_hash_deduplication()
    test_orchestrator_blocks_duplicate_signal()
    print("Sprint 24 Signal Factory Verified")


if __name__ == "__main__":
    main()
