import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from src.analysis.scoring import ScoringEngine
from src.core.orchestrator import PulseOrchestrator
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def test_perfect_score_actionable() -> None:
    engine = ScoringEngine()

    score = engine.calculate_total_score(
        trade_direction="LONG",
        macro_bias="BIAS_LONG",
        current_structure="BULLISH",
        zone_dict={"status": "ACTIVE"},
        has_recent_sweep=True,
    )

    assert score == 100
    assert engine.classify_score(100) == "ACTIONABLE"


def test_mediocre_score_watchlist() -> None:
    engine = ScoringEngine()

    score = engine.calculate_total_score(
        trade_direction="SHORT",
        macro_bias="NEUTRAL",
        current_structure="BEARISH",
        zone_dict={"status": "ACTIVE"},
        has_recent_sweep=False,
    )

    assert score == 55
    assert engine.classify_score(55) == "WATCHLIST"


def test_rejected_score() -> None:
    engine = ScoringEngine()

    score = engine.calculate_total_score(
        trade_direction="LONG",
        macro_bias="BIAS_SHORT",
        current_structure="BEARISH",
        zone_dict={"status": "INVALIDATED"},
        has_recent_sweep=False,
    )

    assert score == 0
    assert engine.classify_score(0) == "REJECTED"


def test_orchestrator_integration_sets_actionable_classification(tmp_path: Path) -> None:
    db_path = tmp_path / "sprint23.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)

    repository.save_zone(
        {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "type": "OB_BULLISH",
            "price_top": 100.0,
            "price_bottom": 95.0,
            "status": "ACTIVE",
            "created_at": 1_700_000_000,
        }
    )
    repository.set_kv("macro_bias_state", "BIAS_LONG")
    repository.set_kv("current_structure_state", "BULLISH")
    repository.set_kv(
        "latest_liquidity_sweep",
        {"type": "LIQUIDITY_SWEEP_LONG", "timestamp": 1_700_000_000},
    )

    # Wrap set_kv so we can assert calls while still persisting to sqlite.
    repository.set_kv = MagicMock(wraps=repository.set_kv)

    latest_candle = Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=1_700_000_060,
        open=99.0,
        high=102.0,
        low=99.5,
        close=101.0,
        volume=200.0,
    )

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            assert symbol == "XAUUSD"
            assert timeframe == "M1"
            return [latest_candle]

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repository,
        client_factory=lambda _: StubClient(),
        memory_profiler=MagicMock(),
    )

    orchestrator._run_macro_regime_check = MagicMock()
    orchestrator._persist_latest_fractals = MagicMock()
    orchestrator._evaluate_liquidity_sweep = MagicMock()
    orchestrator._evaluate_market_structure = MagicMock(return_value=None)
    orchestrator._scan_for_fvg_zones = MagicMock(return_value=[])
    orchestrator._scan_for_order_blocks = MagicMock()

    orchestrator.run()

    repository.set_kv.assert_any_call("latest_setup_classification", "ACTIONABLE")


if __name__ == "__main__":
    test_perfect_score_actionable()
    test_mediocre_score_watchlist()
    test_rejected_score()
    with TemporaryDirectory() as temp_dir:
        test_orchestrator_integration_sets_actionable_classification(Path(temp_dir))

    print("Sprint 23 Confluence Scoring Verified")
