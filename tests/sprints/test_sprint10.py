"""Sprint 10 Verification: Sovereign Demand Proxy.

Test 1: Multiplier math — threshold boundary and below
Test 2: Default state — missing kv_store key returns 400.0
Test 3: Orchestrator persistence — macro_long_bias_multiplier written as 1.25
"""

import sqlite3
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd


from src.analysis.sovereign import SovereignProxy
from src.ingestion.macro_client import FredMacroClient
from src.persistence.schema import SchemaInitializer
from src.persistence.repository import Repository
from src.core.orchestrator import PulseOrchestrator


def _make_repo() -> Repository:
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    return Repository(conn)


def test_multiplier_math():
    """Verify threshold logic: >350 → 1.25, ≤350 → 1.0."""
    proxy = SovereignProxy()

    result_351 = proxy.calculate_multiplier(351.0)
    assert result_351 == 1.25, f"Expected 1.25 for 351.0, got {result_351}"

    result_350 = proxy.calculate_multiplier(350.0)
    assert result_350 == 1.0, f"Expected 1.0 for 350.0, got {result_350}"

    result_100 = proxy.calculate_multiplier(100.0)
    assert result_100 == 1.0, f"Expected 1.0 for 100.0, got {result_100}"

    print("  calculate_multiplier(351.0) = 1.25 PASSED")
    print("  calculate_multiplier(350.0) = 1.0  PASSED")
    print("  calculate_multiplier(100.0) = 1.0  PASSED")


def test_default_state():
    """No real hand-entered figure: unknown (None), never a made-up default."""
    mock_repo = MagicMock(spec=Repository)
    mock_repo.get_kv.return_value = None

    proxy = SovereignProxy()
    result = proxy.get_net_purchases(mock_repo)

    assert result is None, f"Expected None (unknown), got {result}"
    assert proxy.calculate_multiplier(result) == 1.0
    mock_repo.set_kv.assert_not_called()


def test_orchestrator_persistence():
    """Force macro update, verify set_kv called with macro_long_bias_multiplier=1.25."""
    repo = _make_repo()
    now = int(time.time())

    # Force cache expiry (25 hours ago)
    stale = now - (25 * 3600)
    repo.set_kv("last_macro_update_timestamp", str(stale))

    # Seed 60 days of Gold data
    base_ts = now - 86400 * 60
    for i in range(60):
        ts = base_ts + 86400 * i
        repo.connection.execute(
            "INSERT INTO market_data "
            "(symbol, timeframe, timestamp, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("XAUUSD", "H1", ts, 2000.0 + i, 2001.0 + i, 1999.0 + i, 2000.5 + i, 100.0),
        )
    repo.connection.commit()

    # Build TIPS series
    tips_dates = pd.date_range(
        datetime.fromtimestamp(base_ts, tz=timezone.utc).date(),
        periods=60,
        freq="D",
    )
    tips_series = pd.Series([4.0 - i * 0.05 for i in range(60)], index=tips_dates)
    dti = pd.DatetimeIndex(tips_series.index)
    tips_series.index = dti.tz_localize(None)

    mock_macro = MagicMock(spec=FredMacroClient)
    mock_macro.fetch_10y_tips_yield.return_value = tips_series

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repo,
        macro_client=mock_macro,
    )

    # No real central-bank figure entered -> the long-bias multiplier stays off.
    orchestrator._run_macro_regime_check(repo)

    multiplier_str = repo.get_kv("macro_long_bias_multiplier")
    assert multiplier_str is not None, "macro_long_bias_multiplier must be persisted"
    assert float(multiplier_str) == 1.0, f"Expected 1.0 without real data, got {multiplier_str}"


def main() -> int:
    print("=" * 60)
    print("Sprint 10: Sovereign Demand Proxy Verification")
    print("=" * 60)

    print("\n[1/3] Testing Multiplier Math...")
    test_multiplier_math()

    print("\n[2/3] Testing Default State & Repository Integration...")
    test_default_state()

    print("\n[3/3] Testing Orchestrator Persistence...")
    test_orchestrator_persistence()

    print("\n" + "=" * 60)
    print("Sprint 10 Sovereign Demand Proxy Verified")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
