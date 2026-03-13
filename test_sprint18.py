import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from src.analysis.structure import MarketStructureEngine
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
) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100.0,
    )


def _build_structure_window() -> list[Candle]:
    base_ts = 1_700_000_000
    candles = [
        _make_candle(base_ts + 0 * 60, 9.2, 10.0, 8.0, 9.0),
        _make_candle(base_ts + 1 * 60, 10.2, 11.0, 7.0, 10.0),
        _make_candle(base_ts + 2 * 60, 14.2, 15.0, 6.0, 14.0),
        _make_candle(base_ts + 3 * 60, 11.2, 12.0, 5.0, 11.0),
        _make_candle(base_ts + 4 * 60, 10.2, 11.0, 4.0, 10.0),
        _make_candle(base_ts + 5 * 60, 15.2, 16.0, 6.0, 15.0),
        _make_candle(base_ts + 6 * 60, 3.2, 14.0, 2.0, 3.0),
        _make_candle(base_ts + 7 * 60, 4.2, 13.0, 4.0, 5.0),
        _make_candle(base_ts + 8 * 60, 5.2, 17.0, 5.0, 6.0),
        _make_candle(base_ts + 9 * 60, 2.4, 4.0, 1.0, 1.5),
    ]
    return candles


def test_detect_bos_uses_body_close_only() -> None:
    engine = MarketStructureEngine()
    swing_high = {"timestamp": 1_700_000_300, "price": 16.0}
    swing_low = {"timestamp": 1_700_000_360, "price": 2.0}

    bullish_close_break = _make_candle(1_700_001_000, 15.5, 16.5, 15.0, 16.1)
    bullish_wick_only = _make_candle(1_700_001_060, 15.5, 16.5, 15.0, 15.9)
    bearish_close_break = _make_candle(1_700_001_120, 2.5, 2.8, 1.5, 1.9)
    bearish_wick_only = _make_candle(1_700_001_180, 2.5, 2.8, 1.5, 2.1)

    assert engine.detect_bos(bullish_close_break, swing_high, "BULLISH") is True
    assert engine.detect_bos(bullish_wick_only, swing_high, "BULLISH") is False
    assert engine.detect_bos(bearish_close_break, swing_low, "BEARISH") is True
    assert engine.detect_bos(bearish_wick_only, swing_low, "BEARISH") is False


def test_detect_choch_uses_body_close_only() -> None:
    engine = MarketStructureEngine()
    swing_high = {"timestamp": 1_700_000_300, "price": 16.0}
    swing_low = {"timestamp": 1_700_000_360, "price": 2.0}

    bullish_reversal = _make_candle(1_700_001_240, 2.4, 2.7, 1.5, 1.9)
    bullish_wick_only = _make_candle(1_700_001_300, 2.4, 2.7, 1.5, 2.2)
    bearish_reversal = _make_candle(1_700_001_360, 15.8, 16.8, 15.0, 16.1)
    bearish_wick_only = _make_candle(1_700_001_420, 15.8, 16.8, 15.0, 15.9)

    assert engine.detect_choch(bullish_reversal, swing_low, "BULLISH") == "BEARISH"
    assert engine.detect_choch(bullish_wick_only, swing_low, "BULLISH") is None
    assert engine.detect_choch(bearish_reversal, swing_high, "BEARISH") == "BULLISH"
    assert engine.detect_choch(bearish_wick_only, swing_high, "BEARISH") is None


def test_orchestrator_updates_structure_state_on_choch(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint18.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    candles = _build_structure_window()

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            assert symbol == "XAUUSD"
            assert timeframe == "M1"
            return candles

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repository,
        client_factory=lambda repo: StubClient(),
        memory_profiler=MagicMock(),
    )
    orchestrator._run_macro_regime_check = MagicMock()

    orchestrator.run()

    verify_connection = sqlite3.connect(str(db_path))
    state_row = verify_connection.execute(
        "SELECT value FROM kv_store WHERE key = 'current_structure_state';"
    ).fetchone()
    low_row = verify_connection.execute(
        "SELECT value FROM kv_store WHERE key = 'last_swing_low';"
    ).fetchone()
    high_row = verify_connection.execute(
        "SELECT value FROM kv_store WHERE key = 'last_swing_high';"
    ).fetchone()

    assert state_row is not None
    assert state_row[0] == "BEARISH"
    assert low_row is not None
    assert high_row is not None
    assert json.loads(low_row[0]) == {
        "timestamp": candles[6].timestamp,
        "price": float(candles[6].low),
    }
    assert json.loads(high_row[0]) == {
        "timestamp": candles[5].timestamp,
        "price": float(candles[5].high),
    }

    verify_connection.close()


def test_orchestrator_checks_choch_before_bos() -> None:
    candles = _build_structure_window()
    repo_mock = MagicMock()
    repo_mock.connection = MagicMock()
    repo_mock.get_recent_candles.return_value = candles

    def get_kv(key: str) -> str | None:
        if key == "current_structure_state":
            return "BULLISH"
        if key == "last_swing_high":
            return json.dumps({"timestamp": candles[5].timestamp, "price": float(candles[5].high)})
        if key == "last_swing_low":
            return json.dumps({"timestamp": candles[6].timestamp, "price": float(candles[6].low)})
        return None

    repo_mock.get_kv.side_effect = get_kv

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            return candles

    with patch("src.core.orchestrator.MarketStructureEngine") as engine_class:
        engine = engine_class.return_value
        engine.detect_choch.return_value = "BEARISH"
        engine.detect_bos.return_value = True

        orchestrator = PulseOrchestrator(
            repository_factory=lambda: repo_mock,
            client_factory=lambda repo: StubClient(),
            memory_profiler=MagicMock(),
        )
        orchestrator._run_macro_regime_check = MagicMock()

        orchestrator.run()

        engine.detect_choch.assert_called_once()
        engine.detect_bos.assert_not_called()
        repo_mock.set_kv.assert_any_call("current_structure_state", "BEARISH")


def test_orchestrator_keeps_trend_on_bos(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint18_bos.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    repository.set_kv("current_structure_state", "BULLISH")
    candles = _build_structure_window()

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            return candles

    with patch("src.core.orchestrator.MarketStructureEngine") as engine_class:
        engine = engine_class.return_value
        engine.detect_choch.return_value = None
        engine.detect_bos.return_value = True

        orchestrator = PulseOrchestrator(
            repository_factory=lambda: repository,
            client_factory=lambda repo: StubClient(),
            memory_profiler=MagicMock(),
        )
        orchestrator._run_macro_regime_check = MagicMock()

        orchestrator.run()

        engine.detect_choch.assert_called_once()
        engine.detect_bos.assert_called_once()

    verify_connection = sqlite3.connect(str(db_path))
    state_row = verify_connection.execute(
        "SELECT value FROM kv_store WHERE key = 'current_structure_state';"
    ).fetchone()
    assert state_row is not None
    assert state_row[0] == "BULLISH"
    verify_connection.close()


def main() -> None:
    test_detect_bos_uses_body_close_only()
    test_detect_choch_uses_body_close_only()
    with TemporaryDirectory() as temp_dir:
        test_orchestrator_updates_structure_state_on_choch(Path(temp_dir))
    test_orchestrator_checks_choch_before_bos()
    with TemporaryDirectory() as temp_dir:
        test_orchestrator_keeps_trend_on_bos(Path(temp_dir))
    print("Sprint 18 Market Structure Verified")


if __name__ == "__main__":
    main()
