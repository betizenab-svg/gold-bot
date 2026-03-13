import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from src.analysis.fractals import FractalDetector
from src.core.orchestrator import PulseOrchestrator
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _make_candle(timestamp: int, high: float, low: float) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=timestamp,
        open=high - 1.0,
        high=high,
        low=low,
        close=low + 1.0,
        volume=100.0,
    )


def _build_fractal_window() -> list[Candle]:
    highs = [10.0, 11.0, 15.0, 12.0, 11.0, 16.0, 14.0, 13.0, 17.0, 18.0]
    lows = [8.0, 7.0, 6.0, 5.0, 4.0, 6.0, 2.0, 4.0, 5.0, 6.0]
    return [
        _make_candle(1_700_000_000 + (index * 60), high, low)
        for index, (high, low) in enumerate(zip(highs, lows))
    ]


def test_fractal_detector_returns_latest_confirmed_swings() -> None:
    candles = _build_fractal_window()
    expected = {
        "swing_high": {
            "timestamp": candles[5].timestamp,
            "price": float(candles[5].high),
        },
        "swing_low": {
            "timestamp": candles[6].timestamp,
            "price": float(candles[6].low),
        },
    }

    result = FractalDetector().find_fractals(candles)

    assert result == expected


def test_repository_get_recent_candles_returns_latest_subset_ascending() -> None:
    connection = sqlite3.connect(":memory:")
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    candles = _build_fractal_window()

    repository.save_candles(candles)

    recent = repository.get_recent_candles("XAUUSD", "M1", limit=4)

    assert [candle.timestamp for candle in recent] == [c.timestamp for c in candles[-4:]]
    assert recent[0].timestamp < recent[-1].timestamp
    assert isinstance(recent[0].timestamp, int)
    assert isinstance(recent[0].high, float)

    connection.close()


def test_orchestrator_persists_latest_fractals_as_json(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint17.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    candles = _build_fractal_window()
    expected = FractalDetector().find_fractals(candles)

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
    stored = verify_connection.execute(
        "SELECT value FROM kv_store WHERE key = 'smc_latest_fractals';"
    ).fetchone()
    assert stored is not None

    payload = json.loads(stored[0])
    assert payload == expected

    verify_connection.close()


def main() -> None:
    test_fractal_detector_returns_latest_confirmed_swings()
    test_repository_get_recent_candles_returns_latest_subset_ascending()
    with TemporaryDirectory() as temp_dir:
        test_orchestrator_persists_latest_fractals_as_json(Path(temp_dir))
    print("Sprint 17 Fractal Algorithms Verified")


if __name__ == "__main__":
    main()
