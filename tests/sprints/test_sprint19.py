import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from src.analysis.atr import ATREngine
from src.analysis.fvg import FVGScanner
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


def _build_atr_window() -> list[Candle]:
    base_ts = 1_700_000_000
    candles: list[Candle] = []
    for index in range(15):
        candles.append(
            _make_candle(
                base_ts + (index * 60),
                10.0,
                12.0,
                8.0,
                10.0,
            )
        )
    return candles


def _build_bullish_fvg_window() -> list[Candle]:
    base_ts = 1_700_000_000
    candles: list[Candle] = []
    for index in range(12):
        candles.append(
            _make_candle(
                base_ts + (index * 60),
                10.0,
                12.0,
                8.0,
                10.0,
            )
        )

    candles.extend(
        [
            _make_candle(base_ts + 12 * 60, 9.5, 10.0, 8.0, 9.0),
            _make_candle(base_ts + 13 * 60, 10.5, 11.0, 9.0, 10.0),
            _make_candle(base_ts + 14 * 60, 13.0, 14.0, 12.5, 13.0),
        ]
    )
    return candles


def test_atr_engine_calculates_14_period_sma_of_true_range() -> None:
    candles = _build_atr_window()
    atr = ATREngine().calculate_atr(candles, period=14)
    assert atr == 4.0, f"Expected ATR 4.0, got {atr}"


def test_fvg_scanner_detects_bullish_gap_from_candle_1_and_3() -> None:
    candles = [
        _make_candle(1_700_000_000, 9.5, 10.0, 8.0, 9.0),
        _make_candle(1_700_000_060, 100.0, 150.0, 1.0, 75.0),
        _make_candle(1_700_000_120, 13.0, 14.0, 12.5, 13.0),
    ]

    result = FVGScanner().detect_fvg(candles, current_atr=4.0)

    assert result == {
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "type": "FVG_BULLISH",
        "price_top": 12.5,
        "price_bottom": 10.0,
        "status": "UNMITIGATED",
    }


def test_fvg_scanner_detects_bearish_gap_from_candle_1_and_3() -> None:
    candles = [
        _make_candle(1_700_000_000, 14.0, 16.0, 15.0, 15.5),
        _make_candle(1_700_000_060, 2.0, 50.0, 1.0, 20.0),
        _make_candle(1_700_000_120, 12.0, 12.7, 11.5, 12.0),
    ]

    result = FVGScanner().detect_fvg(candles, current_atr=4.0)

    assert result == {
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "type": "FVG_BEARISH",
        "price_top": 15.0,
        "price_bottom": 12.7,
        "status": "UNMITIGATED",
    }


def test_fvg_scanner_rejects_gap_at_or_below_half_atr() -> None:
    candles = [
        _make_candle(1_700_000_000, 9.5, 10.0, 8.0, 9.0),
        _make_candle(1_700_000_060, 10.5, 11.0, 9.0, 10.0),
        _make_candle(1_700_000_120, 12.0, 13.0, 12.0, 12.4),
    ]

    result = FVGScanner().detect_fvg(candles, current_atr=4.0)

    assert result is None


def test_orchestrator_persists_unmitigated_fvg_zone(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint19.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    candles = _build_bullish_fvg_window()

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
    row = verify_connection.execute(
        """
        SELECT symbol, timeframe, type, price_top, price_bottom, status
        FROM zones
        ORDER BY id DESC
        LIMIT 1;
        """
    ).fetchone()

    assert row is not None
    assert row[0] == "XAUUSD"
    assert row[1] == "M1"
    assert row[2] == "FVG_BULLISH"
    assert row[3] == 12.5
    assert row[4] == 10.0
    assert row[5] == "UNMITIGATED"

    verify_connection.close()


def main() -> None:
    test_atr_engine_calculates_14_period_sma_of_true_range()
    test_fvg_scanner_detects_bullish_gap_from_candle_1_and_3()
    test_fvg_scanner_detects_bearish_gap_from_candle_1_and_3()
    test_fvg_scanner_rejects_gap_at_or_below_half_atr()
    with TemporaryDirectory() as temp_dir:
        test_orchestrator_persists_unmitigated_fvg_zone(Path(temp_dir))
    print("Sprint 19 Fair Value Gap Scanner Verified")


if __name__ == "__main__":
    main()
