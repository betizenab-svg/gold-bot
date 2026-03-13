import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from src.analysis.mitigation import ZoneLifecycleManager
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


def test_zone_lifecycle_manager_updates_bullish_zones() -> None:
    current_candle = _make_candle(1_700_000_000, 100.0, 106.0, 94.0, 99.0)
    zones = [
        {
            "id": 1,
            "type": "FVG_BULLISH",
            "price_top": 95.0,
            "price_bottom": 90.0,
            "status": "UNMITIGATED",
        },
        {
            "id": 2,
            "type": "OB_BULLISH",
            "price_top": 101.0,
            "price_bottom": 100.0,
            "status": "ACTIVE",
        },
        {
            "id": 3,
            "type": "OB_BULLISH",
            "price_top": 93.0,
            "price_bottom": 88.0,
            "status": "ACTIVE",
        },
    ]

    result = ZoneLifecycleManager().evaluate_zones(current_candle, zones)

    assert result == [
        {
            "id": 1,
            "type": "FVG_BULLISH",
            "price_top": 95.0,
            "price_bottom": 90.0,
            "status": "MITIGATED",
        },
        {
            "id": 2,
            "type": "OB_BULLISH",
            "price_top": 101.0,
            "price_bottom": 100.0,
            "status": "INVALIDATED",
        },
    ]


def test_zone_lifecycle_manager_updates_bearish_zones() -> None:
    current_candle = _make_candle(1_700_000_060, 100.0, 111.0, 96.0, 108.0)
    zones = [
        {
            "id": 4,
            "type": "FVG_BEARISH",
            "price_top": 110.0,
            "price_bottom": 105.0,
            "status": "UNMITIGATED",
        },
        {
            "id": 5,
            "type": "OB_BEARISH",
            "price_top": 107.0,
            "price_bottom": 103.0,
            "status": "ACTIVE",
        },
        {
            "id": 6,
            "type": "OB_BEARISH",
            "price_top": 115.0,
            "price_bottom": 112.0,
            "status": "ACTIVE",
        },
    ]

    result = ZoneLifecycleManager().evaluate_zones(current_candle, zones)

    assert result == [
        {
            "id": 4,
            "type": "FVG_BEARISH",
            "price_top": 110.0,
            "price_bottom": 105.0,
            "status": "MITIGATED",
        },
        {
            "id": 5,
            "type": "OB_BEARISH",
            "price_top": 107.0,
            "price_bottom": 103.0,
            "status": "INVALIDATED",
        },
    ]


def test_repository_get_active_zones_fetches_active_and_unmitigated() -> None:
    connection = sqlite3.connect(":memory:")
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    zones = [
        {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "type": "OB_BULLISH",
            "price_top": 101.0,
            "price_bottom": 100.0,
            "status": "ACTIVE",
            "created_at": 1_700_000_000,
        },
        {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "type": "FVG_BEARISH",
            "price_top": 110.0,
            "price_bottom": 105.0,
            "status": "UNMITIGATED",
            "created_at": 1_700_000_060,
        },
        {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "type": "OB_BEARISH",
            "price_top": 115.0,
            "price_bottom": 112.0,
            "status": "MITIGATED",
            "created_at": 1_700_000_120,
        },
    ]
    for zone in zones:
        repository.save_zone(zone)

    result = repository.get_active_zones("XAUUSD")

    assert {zone["status"] for zone in result} == {"ACTIVE", "UNMITIGATED"}
    assert len(result) == 2
    connection.close()


def test_update_zone_statuses_uses_executemany() -> None:
    connection = MagicMock()
    repository = Repository(connection)

    repository.update_zone_statuses(
        [
            {"id": 1, "status": "MITIGATED"},
            {"id": 2, "status": "INVALIDATED"},
        ]
    )

    connection.executemany.assert_called_once()
    connection.commit.assert_called_once()


def test_orchestrator_updates_zone_statuses_in_database(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint21.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)

    repository.save_zone(
        {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "type": "FVG_BULLISH",
            "price_top": 95.0,
            "price_bottom": 90.0,
            "status": "UNMITIGATED",
            "created_at": 1_700_000_000,
        }
    )
    repository.save_zone(
        {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "type": "OB_BEARISH",
            "price_top": 107.0,
            "price_bottom": 103.0,
            "status": "ACTIVE",
            "created_at": 1_700_000_060,
        }
    )

    current_candle = _make_candle(1_700_000_120, 100.0, 111.0, 94.0, 108.0)

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            assert symbol == "XAUUSD"
            assert timeframe == "M1"
            return [current_candle]

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repository,
        client_factory=lambda repo: StubClient(),
        memory_profiler=MagicMock(),
    )
    orchestrator._run_macro_regime_check = MagicMock()
    orchestrator._persist_latest_fractals = MagicMock()
    orchestrator._evaluate_market_structure = MagicMock(return_value=None)
    orchestrator._scan_for_fvg_zones = MagicMock(return_value=[])
    orchestrator._scan_for_order_blocks = MagicMock()

    orchestrator.run()

    verify_connection = sqlite3.connect(str(db_path))
    rows = verify_connection.execute(
        """
        SELECT type, status
        FROM zones
        ORDER BY id ASC;
        """
    ).fetchall()

    assert rows == [
        ("FVG_BULLISH", "MITIGATED"),
        ("OB_BEARISH", "INVALIDATED"),
    ]

    verify_connection.close()


def main() -> None:
    test_zone_lifecycle_manager_updates_bullish_zones()
    test_zone_lifecycle_manager_updates_bearish_zones()
    test_repository_get_active_zones_fetches_active_and_unmitigated()
    test_update_zone_statuses_uses_executemany()
    with TemporaryDirectory() as temp_dir:
        test_orchestrator_updates_zone_statuses_in_database(Path(temp_dir))
    print("Sprint 21 Zone Lifecycle Manager Verified")


if __name__ == "__main__":
    main()
