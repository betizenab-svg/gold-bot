"""Sprint 9 Verification: Regime Detection & Caching.

Scenario A: Perfectly inversely correlated series  -> REGIME_NORMAL
Scenario B: Perfectly positively correlated series -> REGIME_DECOUPLED
Scenario C: Fresh 24h cache prevents macro fetch
Scenario D: Expired 24h cache triggers macro fetch and kv_store update
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd



from src.analysis.regime import RegimeDetector
from src.ingestion.macro_client import FredMacroClient
from src.persistence.schema import SchemaInitializer
from src.persistence.repository import Repository
from src.core.orchestrator import PulseOrchestrator, MACRO_CACHE_TTL_SECONDS


def _make_repo() -> Repository:
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    return Repository(conn)


def test_correlation_scenario_a():
    """Scenario A (Normal): Gold inversely proportional to TIPS -> ~-1.0, REGIME_NORMAL."""
    dates = pd.date_range("2025-01-01", periods=60, freq="D")
    gold = pd.Series([2000.0 + i for i in range(60)], index=dates)
    tips = pd.Series([4.0 - i * 0.05 for i in range(60)], index=dates)

    detector = RegimeDetector()
    correlation = detector.calculate_correlation(gold, tips)
    regime = detector.determine_regime(correlation)

    assert abs(correlation - (-1.0)) < 0.01, f"Expected ~-1.0, got {correlation}"
    assert regime == "REGIME_NORMAL", f"Expected REGIME_NORMAL, got {regime}"
    print(f"  Scenario A PASSED: correlation={correlation:.4f}, regime={regime}")


def test_correlation_scenario_b():
    """Scenario B (Decoupled): Gold proportional to TIPS -> ~1.0, REGIME_DECOUPLED."""
    dates = pd.date_range("2025-01-01", periods=60, freq="D")
    gold = pd.Series([2000.0 + i for i in range(60)], index=dates)
    tips = pd.Series([2.0 + i * 0.05 for i in range(60)], index=dates)

    detector = RegimeDetector()
    correlation = detector.calculate_correlation(gold, tips)
    regime = detector.determine_regime(correlation)

    assert abs(correlation - 1.0) < 0.01, f"Expected ~1.0, got {correlation}"
    assert regime == "REGIME_DECOUPLED", f"Expected REGIME_DECOUPLED, got {regime}"
    print(f"  Scenario B PASSED: correlation={correlation:.4f}, regime={regime}")


def test_cache_fresh_skips_fetch():
    """Cache set 10 minutes ago -> fetch_10y_tips_yield must NOT be called."""
    repo = _make_repo()
    now = int(time.time())
    repo.set_kv("last_macro_update_timestamp", str(now - 600))  # 10 minutes ago

    mock_macro = MagicMock(spec=FredMacroClient)
    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repo,
        macro_client=mock_macro,
    )

    orchestrator._run_macro_regime_check(repo)

    assert not mock_macro.fetch_10y_tips_yield.called, (
        "fetch_10y_tips_yield should NOT be called with a fresh cache"
    )
    assert repo.get_kv("macro_regime") is None, (
        "macro_regime should NOT be written when cache is fresh"
    )
    print("  Cache-fresh skip PASSED: macro fetch was correctly skipped")


def test_cache_expired_triggers_fetch():
    """Cache set 25 hours ago -> fetch must be called and kv_store updated."""
    repo = _make_repo()
    now = int(time.time())
    stale = now - (25 * 3600)  # 25 hours ago
    repo.set_kv("last_macro_update_timestamp", str(stale))

    # Seed 60 days of Gold data so _fetch_gold_daily_closes returns a series
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

    # Build a TIPS series aligned to the same date range
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

    orchestrator._run_macro_regime_check(repo)

    assert mock_macro.fetch_10y_tips_yield.called, (
        "fetch_10y_tips_yield SHOULD be called with an expired cache"
    )

    regime = repo.get_kv("macro_regime")
    assert regime is not None, "macro_regime must be persisted"
    assert regime in ("REGIME_NORMAL", "REGIME_DECOUPLED", "REGIME_TRANSITION", "REGIME_UNKNOWN"), (
        f"Unexpected regime: {regime}"
    )

    corr_str = repo.get_kv("macro_tips_correlation")
    assert corr_str is not None, "macro_tips_correlation must be persisted"
    corr_val = float(corr_str)
    assert -1.0 <= corr_val <= 1.0, f"Correlation out of range: {corr_val}"

    ts_str = repo.get_kv("last_macro_update_timestamp")
    assert ts_str is not None, "last_macro_update_timestamp must be updated"
    assert int(ts_str) > stale, "Timestamp should be refreshed"

    print(
        f"  Cache-expired trigger PASSED: regime={regime}, "
        f"correlation={corr_val:.4f}, timestamp refreshed"
    )


def main() -> int:
    print("=" * 60)
    print("Sprint 9: Regime Detection & Caching Verification")
    print("=" * 60)

    print("\n[1/2] Testing Correlation Math...")
    test_correlation_scenario_a()
    test_correlation_scenario_b()

    print("\n[2/2] Testing Orchestrator Caching...")
    test_cache_fresh_skips_fetch()
    test_cache_expired_triggers_fetch()

    print("\n" + "=" * 60)
    print("Sprint 9 Regime Detection & Caching Verified")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
