import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from src.analysis.displacement import DisplacementEngine
from src.analysis.order_block import OrderBlockScanner
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


def _build_body_window() -> list[Candle]:
    base_ts = 1_700_000_000
    return [
        _make_candle(base_ts + (index * 60), 10.0, 12.0, 8.0, 12.0)
        for index in range(15)
    ]


def _build_bullish_ob_window() -> list[Candle]:
    base_ts = 1_700_000_000
    candles: list[Candle] = []
    for index in range(12):
        candles.append(
            _make_candle(
                base_ts + (index * 60),
                9.0,
                10.0,
                8.0,
                9.5,
            )
        )

    candles.extend(
        [
            _make_candle(base_ts + 12 * 60, 9.5, 10.0, 8.0, 8.5),
            _make_candle(base_ts + 13 * 60, 9.5, 14.0, 9.0, 12.5),
            _make_candle(base_ts + 14 * 60, 13.0, 17.0, 14.5, 17.0),
        ]
    )
    return candles


def test_calculate_average_body_uses_14_period_sma() -> None:
    candles = _build_body_window()
    avg_body = DisplacementEngine().calculate_average_body(candles, period=14)
    assert avg_body == 2.0, f"Expected average body 2.0, got {avg_body}"


def test_detect_displacement_uses_strict_1_5_multiplier() -> None:
    engine = DisplacementEngine()
    equal_threshold = _make_candle(1_700_001_000, 10.0, 13.0, 9.0, 13.0)
    above_threshold = _make_candle(1_700_001_060, 10.0, 13.2, 9.0, 13.2)

    assert engine.detect_displacement(equal_threshold, 2.0) is False
    assert engine.detect_displacement(above_threshold, 2.0) is True


def test_detect_order_block_returns_bullish_ob_from_last_opposing_candle() -> None:
    candles = _build_bullish_ob_window()
    recent_fvgs = [
        {
            "type": "FVG_BULLISH",
            "status": "UNMITIGATED",
            "created_at": candles[14].timestamp,
        }
    ]

    result = OrderBlockScanner().detect_order_block(
        candles=candles,
        recent_bos_type="BULLISH",
        recent_fvgs=recent_fvgs,
        avg_body=1.0,
    )

    assert result == {
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "type": "OB_BULLISH",
        "price_top": float(candles[12].high),
        "price_bottom": float(candles[12].low),
        "status": "ACTIVE",
    }


def test_detect_order_block_rejects_missing_displacement() -> None:
    candles = _build_bullish_ob_window()
    recent_fvgs = [
        {
            "type": "FVG_BULLISH",
            "status": "UNMITIGATED",
            "created_at": candles[14].timestamp,
        }
    ]

    result = OrderBlockScanner().detect_order_block(
        candles=candles,
        recent_bos_type="BULLISH",
        recent_fvgs=recent_fvgs,
        avg_body=3.0,
    )

    assert result is None


def test_detect_order_block_rejects_missing_associated_fvg() -> None:
    candles = _build_bullish_ob_window()

    result = OrderBlockScanner().detect_order_block(
        candles=candles,
        recent_bos_type="BULLISH",
        recent_fvgs=[],
        avg_body=1.0,
    )

    assert result is None


def test_orchestrator_persists_active_order_block_zone(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint20.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    candles = _build_bullish_ob_window()

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
    orchestrator._evaluate_market_structure = MagicMock(return_value="BULLISH")

    orchestrator.run()

    verify_connection = sqlite3.connect(str(db_path))
    row = verify_connection.execute(
        """
        SELECT symbol, timeframe, type, price_top, price_bottom, status
        FROM zones
        WHERE type = 'OB_BULLISH'
        ORDER BY id DESC
        LIMIT 1;
        """
    ).fetchone()

    assert row is not None
    assert row[0] == "XAUUSD"
    assert row[1] == "M1"
    assert row[2] == "OB_BULLISH"
    assert row[3] == float(candles[12].high)
    assert row[4] == float(candles[12].low)
    assert row[5] == "ACTIVE"

    verify_connection.close()


def main() -> None:
    test_calculate_average_body_uses_14_period_sma()
    test_detect_displacement_uses_strict_1_5_multiplier()
    test_detect_order_block_returns_bullish_ob_from_last_opposing_candle()
    test_detect_order_block_rejects_missing_displacement()
    test_detect_order_block_rejects_missing_associated_fvg()
    with TemporaryDirectory() as temp_dir:
        test_orchestrator_persists_active_order_block_zone(Path(temp_dir))
    print("Sprint 20 Order Block Detection Verified")


if __name__ == "__main__":
    main()
