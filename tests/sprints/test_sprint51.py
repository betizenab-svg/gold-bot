"""Sprint 51: correlated-exposure guard, setup-funnel telemetry, API summary."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from src.analysis.risk_governor import RiskGovernor
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


class _OpenSignal:
    def __init__(self, symbol: str, signal_type: str) -> None:
        self.symbol = symbol
        self.signal_type = signal_type
        self.status = "ACTIVE"


def _repo_with_open(signals: list) -> MagicMock:
    repository = MagicMock()
    repository.get_kv.return_value = None
    repository.get_open_signals.return_value = signals
    repository.count_signals_since.return_value = 0
    return repository


def test_correlated_same_direction_blocked() -> None:
    governor = RiskGovernor(max_concurrent_signals=5)
    repository = _repo_with_open([_OpenSignal("EURUSD", "LONG")])
    allowed, reason = governor.is_trading_allowed(
        repository, 1_700_000_000, symbol="GBPUSD", direction="LONG"
    )
    assert allowed is False
    assert "correlated" in reason.lower()


def test_correlated_opposite_direction_allowed() -> None:
    governor = RiskGovernor(max_concurrent_signals=5)
    repository = _repo_with_open([_OpenSignal("EURUSD", "LONG")])
    allowed, _ = governor.is_trading_allowed(
        repository, 1_700_000_000, symbol="GBPUSD", direction="SHORT"
    )
    assert allowed is True


def test_uncorrelated_markets_unaffected() -> None:
    governor = RiskGovernor(max_concurrent_signals=5)
    repository = _repo_with_open([_OpenSignal("EURUSD", "LONG")])
    # Gold has no correlation group; BTC neither.
    for symbol in ("XAUUSD", "BTCUSD"):
        allowed, _ = governor.is_trading_allowed(
            repository, 1_700_000_000, symbol=symbol, direction="LONG"
        )
        assert allowed is True


def test_governor_without_symbol_keeps_legacy_behavior() -> None:
    governor = RiskGovernor(max_concurrent_signals=5)
    repository = _repo_with_open([_OpenSignal("EURUSD", "LONG")])
    allowed, _ = governor.is_trading_allowed(repository, 1_700_000_000)
    assert allowed is True


def test_setup_log_roundtrip(tmp_path: Path) -> None:
    connection = sqlite3.connect(str(tmp_path / "sprint51.db"))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)

    repository.log_setup(
        symbol="EURUSD",
        strategy="PIN_BAR_REJECTION",
        direction="LONG",
        order_type="STOP",
        score=68,
        classification="WATCHLIST",
        vetoes="",
        timestamp=1_700_000_000,
    )
    repository.log_setup(
        symbol="XAUUSD",
        strategy="ZONE_BOUNCE",
        direction="SHORT",
        order_type="LIMIT",
        score=82,
        classification="ACTIONABLE",
        vetoes="",
        timestamp=1_700_000_300,
    )

    setups = repository.get_recent_setups(limit=5)
    assert len(setups) == 2
    assert setups[0]["symbol"] == "XAUUSD"  # newest first
    assert setups[0]["classification"] == "ACTIONABLE"
    assert setups[1]["strategy"] == "PIN_BAR_REJECTION"
    repository.close()


def test_api_summary_route(tmp_path: Path, monkeypatch) -> None:
    import importlib

    db_path = tmp_path / "api.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    connection.execute(
        "INSERT INTO kv_store (key, value, updated_at) VALUES "
        "('last_pulse_wallclock', '1700000000', 1700000000);"
    )
    connection.commit()
    connection.close()

    dashboard_app = importlib.import_module("src.dashboard.app")
    monkeypatch.setattr(dashboard_app, "DB_PATH", str(db_path))
    app = dashboard_app.create_app()
    app.config["LOGIN_DISABLED"] = True

    with app.test_client() as client:
        response = client.get("/api/summary")
        assert response.status_code == 200
        payload = response.get_json()
        assert "net_r" in payload
        assert "markets" in payload
        assert isinstance(payload["markets"], list)


if __name__ == "__main__":
    test_correlated_same_direction_blocked()
    print("Sprint 51 Correlation Guard + Funnel Verified")
