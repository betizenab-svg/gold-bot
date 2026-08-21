import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from src.analysis.liquidity import LiquiditySweepDetector
from src.core.orchestrator import PulseOrchestrator
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


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


def _build_volume_window() -> list[Candle]:
    base_ts = 1_700_000_000
    return [
        _make_candle(base_ts + (index * 60), 100.0, 102.0, 98.0, 100.5, 100.0)
        for index in range(14)
    ]


def _build_orchestrator_window() -> list[Candle]:
    base_ts = 1_700_000_000
    candles = [
        _make_candle(base_ts + 0 * 60, 10.0, 11.0, 9.5, 10.2, 100.0),
        _make_candle(base_ts + 1 * 60, 10.2, 12.0, 10.0, 11.0, 100.0),
        _make_candle(base_ts + 2 * 60, 11.0, 14.0, 10.8, 13.0, 100.0),
        _make_candle(base_ts + 3 * 60, 12.0, 13.0, 10.9, 11.2, 100.0),
        _make_candle(base_ts + 4 * 60, 11.2, 12.5, 10.7, 11.0, 100.0),
        _make_candle(base_ts + 5 * 60, 13.0, 15.5, 12.8, 15.0, 100.0),
        _make_candle(base_ts + 6 * 60, 15.0, 15.2, 10.0, 10.4, 100.0),
        _make_candle(base_ts + 7 * 60, 10.4, 11.8, 10.1, 10.7, 100.0),
        _make_candle(base_ts + 8 * 60, 10.7, 12.0, 10.5, 11.0, 100.0),
        _make_candle(base_ts + 9 * 60, 11.0, 12.2, 10.8, 11.3, 100.0),
        _make_candle(base_ts + 10 * 60, 11.3, 12.5, 11.1, 11.6, 100.0),
        _make_candle(base_ts + 11 * 60, 11.6, 12.8, 11.4, 11.9, 100.0),
        _make_candle(base_ts + 12 * 60, 11.9, 13.0, 11.7, 12.1, 100.0),
        _make_candle(base_ts + 13 * 60, 12.1, 13.2, 11.9, 12.4, 100.0),
        _make_candle(base_ts + 14 * 60, 12.4, 12.8, 9.8, 10.2, 150.0),
    ]
    return candles


def test_calculate_average_volume_returns_14_period_sma() -> None:
    avg_volume = LiquiditySweepDetector().calculate_average_volume(_build_volume_window(), period=14)
    assert avg_volume == 100.0, f"Expected average volume 100.0, got {avg_volume}"


def test_detect_sweep_identifies_liquidity_sweep_long() -> None:
    current_candle = _make_candle(1_700_001_000, 100.0, 101.0, 94.0, 96.0, 130.0)

    result = LiquiditySweepDetector().detect_sweep(
        current_candle=current_candle,
        avg_volume=100.0,
        last_swing_high=110.0,
        last_swing_low=95.0,
    )

    assert result == {
        "type": "LIQUIDITY_SWEEP_LONG",
        "sweep_price": 94.0,
        "timestamp": current_candle.timestamp,
    }


def test_detect_sweep_identifies_liquidity_sweep_short() -> None:
    current_candle = _make_candle(1_700_001_060, 100.0, 111.0, 99.0, 109.0, 130.0)

    result = LiquiditySweepDetector().detect_sweep(
        current_candle=current_candle,
        avg_volume=100.0,
        last_swing_high=110.0,
        last_swing_low=95.0,
    )

    assert result == {
        "type": "LIQUIDITY_SWEEP_SHORT",
        "sweep_price": 111.0,
        "timestamp": current_candle.timestamp,
    }


def test_detect_sweep_rejects_volume_at_or_below_threshold() -> None:
    equal_volume_candle = _make_candle(1_700_001_120, 100.0, 101.0, 94.0, 96.0, 120.0)
    below_volume_candle = _make_candle(1_700_001_180, 100.0, 111.0, 99.0, 109.0, 119.0)
    detector = LiquiditySweepDetector()

    assert detector.detect_sweep(equal_volume_candle, 100.0, 110.0, 95.0) is None
    assert detector.detect_sweep(below_volume_candle, 100.0, 110.0, 95.0) is None


def test_orchestrator_persists_latest_liquidity_sweep_to_kv_store(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint22.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    candles = _build_orchestrator_window()

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            assert symbol == "XAUUSD"
            assert timeframe == "M1"
            return candles

    def persist_fractals_side_effect(
        repo: Repository, latest_fractals: dict, symbol: str = "XAUUSD"
    ) -> None:
        repo.set_kv("last_swing_high", {"timestamp": candles[5].timestamp, "price": float(candles[5].high)})
        repo.set_kv("last_swing_low", {"timestamp": candles[6].timestamp, "price": float(candles[6].low)})

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repository,
        client_factory=lambda repo: StubClient(),
        memory_profiler=MagicMock(),
    )
    orchestrator._run_macro_regime_check = MagicMock()
    orchestrator._persist_latest_fractals = MagicMock(side_effect=persist_fractals_side_effect)
    orchestrator._evaluate_market_structure = MagicMock(return_value=None)
    orchestrator._scan_for_fvg_zones = MagicMock(return_value=[])
    orchestrator._scan_for_order_blocks = MagicMock()

    orchestrator.run()

    verify_connection = sqlite3.connect(str(db_path))
    row = verify_connection.execute(
        "SELECT value FROM kv_store WHERE key = 'latest_liquidity_sweep';"
    ).fetchone()

    assert row is not None
    payload = json.loads(row[0])
    assert payload == {
        "type": "LIQUIDITY_SWEEP_LONG",
        "sweep_price": float(candles[14].low),
        "timestamp": int(candles[14].timestamp),
    }

    verify_connection.close()


def main() -> None:
    test_calculate_average_volume_returns_14_period_sma()
    test_detect_sweep_identifies_liquidity_sweep_long()
    test_detect_sweep_identifies_liquidity_sweep_short()
    test_detect_sweep_rejects_volume_at_or_below_threshold()
    with TemporaryDirectory() as temp_dir:
        test_orchestrator_persists_latest_liquidity_sweep_to_kv_store(Path(temp_dir))
    print("Sprint 22 Liquidity Sweeps Verified")


if __name__ == "__main__":
    main()
