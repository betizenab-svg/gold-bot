"""Sprint 11 Verification: Crisis Filter (DXY Correlation Matrix).

Scenario A (Normal): Inversely correlated Gold/DXY -> corr ~ -1.0, crisis_mode = False
Scenario B (Crisis): Positively correlated Gold/DXY -> corr ~ 1.0, crisis_mode = True
Orchestrator: Force macro update, assert macro_dxy_correlation and macro_crisis_mode written
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd


from src.analysis.crisis import CrisisDetector
from src.ingestion.macro_client import FredMacroClient
from src.persistence.schema import SchemaInitializer
from src.persistence.repository import Repository
from src.core.orchestrator import PulseOrchestrator


def _make_repo() -> Repository:
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    return Repository(conn)


def test_scenario_a_normal():
    """Gold inversely proportional to DXY -> corr ~ -1.0, crisis_mode = False."""
    dates = pd.date_range("2025-01-01", periods=30, freq="D")
    gold = pd.Series([2000.0 + i * 10 for i in range(30)], index=dates)
    dxy = pd.Series([110.0 - i * 0.5 for i in range(30)], index=dates)

    detector = CrisisDetector()
    correlation = detector.calculate_dxy_correlation(gold, dxy)
    crisis_mode = detector.evaluate_crisis_mode(correlation)

    assert abs(correlation - (-1.0)) < 0.01, f"Expected ~-1.0, got {correlation}"
    assert crisis_mode is False, f"Expected False (Normal), got {crisis_mode}"
    print(f"  Scenario A PASSED: correlation={correlation:.4f}, crisis_mode={crisis_mode}")


def test_scenario_b_crisis():
    """Gold proportional to DXY -> corr ~ 1.0, crisis_mode = True."""
    dates = pd.date_range("2025-01-01", periods=30, freq="D")
    gold = pd.Series([2000.0 + i * 10 for i in range(30)], index=dates)
    dxy = pd.Series([100.0 + i * 0.5 for i in range(30)], index=dates)

    detector = CrisisDetector()
    correlation = detector.calculate_dxy_correlation(gold, dxy)
    crisis_mode = detector.evaluate_crisis_mode(correlation)

    assert abs(correlation - 1.0) < 0.01, f"Expected ~1.0, got {correlation}"
    assert crisis_mode is True, f"Expected True (Crisis), got {crisis_mode}"
    print(f"  Scenario B PASSED: correlation={correlation:.4f}, crisis_mode={crisis_mode}")


def test_orchestrator_integration():
    """Force macro update, assert macro_dxy_correlation and macro_crisis_mode written."""
    repo = _make_repo()
    now = int(time.time())

    # Force cache expiry (25 hours ago)
    stale = now - (25 * 3600)
    repo.set_kv("last_macro_update_timestamp", str(stale))

    # Seed 60 days of Gold data in market_data
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

    # Mock TIPS series for regime detection
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

    # Mock DXY daily closes (returned by _fetch_dxy_daily_closes)
    dxy_dates = pd.date_range(
        datetime.fromtimestamp(now - 86400 * 30, tz=timezone.utc).date(),
        periods=20,
        freq="D",
    )
    dxy_series = pd.Series([105.0 + i * 0.3 for i in range(20)], index=dxy_dates)
    dti = pd.DatetimeIndex(dxy_series.index)
    dxy_series.index = dti.tz_localize(None)

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repo,
        macro_client=mock_macro,
    )
    orchestrator._fetch_dxy_daily_closes = MagicMock(return_value=dxy_series)

    orchestrator._run_macro_regime_check(repo)

    # Assert macro_dxy_correlation was written
    corr_str = repo.get_kv("macro_dxy_correlation")
    assert corr_str is not None, "macro_dxy_correlation must be persisted"
    corr_val = float(corr_str)
    assert -1.0 <= corr_val <= 1.0, f"Correlation out of range: {corr_val}"

    # Assert macro_crisis_mode was written
    crisis_str = repo.get_kv("macro_crisis_mode")
    assert crisis_str is not None, "macro_crisis_mode must be persisted"
    assert crisis_str in ("0", "1"), f"Expected '0' or '1', got '{crisis_str}'"

    print(
        f"  Orchestrator PASSED: macro_dxy_correlation={corr_val:.4f}, "
        f"macro_crisis_mode={crisis_str}"
    )


def main() -> int:
    print("=" * 60)
    print("Sprint 11: Crisis Filter Verification")
    print("=" * 60)

    print("\n[1/2] Testing Correlation and Mode Logic...")
    test_scenario_a_normal()
    test_scenario_b_crisis()

    print("\n[2/2] Testing Orchestrator Integration...")
    test_orchestrator_integration()

    print("\n" + "=" * 60)
    print("Sprint 11 Crisis Filter Verified")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
