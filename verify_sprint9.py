"""Sprint 9 Verification: Regime Detection Logic.

Tests:
1. RegimeDetector.calculate_correlation — synthetic perfectly negatively correlated series
2. RegimeDetector.determine_regime — threshold mapping
3. Inner join alignment — misaligned dates produce no NaN corruption
4. 24-hour cache gating — fresh cache skips macro update
5. Cache expired flow — stale cache triggers full regime update
6. FredMacroClient — DFII10 series name, return type validation
"""

import math
import os
import sqlite3
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np



from src.analysis.regime import RegimeDetector
from src.ingestion.macro_client import FredMacroClient, DFII10_SERIES
from src.persistence.schema import SchemaInitializer
from src.persistence.repository import Repository
from src.core.orchestrator import PulseOrchestrator, MACRO_CACHE_TTL_SECONDS


def _make_in_memory_repo() -> Repository:
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    return Repository(conn)


class TestRegimeDetectorCorrelation(unittest.TestCase):
    """Test calculate_correlation with synthetic data."""

    def test_perfect_negative_correlation(self):
        dates = pd.date_range("2025-01-01", periods=60, freq="D")
        gold = pd.Series(range(60), index=dates, dtype=float)
        tips = pd.Series(range(59, -1, -1), index=dates, dtype=float)

        detector = RegimeDetector()
        corr = detector.calculate_correlation(gold, tips)

        self.assertAlmostEqual(corr, -1.0, places=5)

    def test_perfect_positive_correlation(self):
        dates = pd.date_range("2025-01-01", periods=60, freq="D")
        gold = pd.Series(range(60), index=dates, dtype=float)
        tips = pd.Series(range(60), index=dates, dtype=float)

        detector = RegimeDetector()
        corr = detector.calculate_correlation(gold, tips)

        self.assertAlmostEqual(corr, 1.0, places=5)

    def test_insufficient_data_returns_nan(self):
        dates = pd.date_range("2025-01-01", periods=1, freq="D")
        gold = pd.Series([100.0], index=dates)
        tips = pd.Series([2.0], index=dates)

        detector = RegimeDetector()
        corr = detector.calculate_correlation(gold, tips)

        self.assertTrue(math.isnan(corr))

    def test_correlation_is_bounded(self):
        np.random.seed(42)
        dates = pd.date_range("2025-01-01", periods=90, freq="D")
        gold = pd.Series(np.random.randn(90).cumsum() + 2000, index=dates)
        tips = pd.Series(np.random.randn(90).cumsum() + 2.0, index=dates)

        detector = RegimeDetector()
        corr = detector.calculate_correlation(gold, tips)

        self.assertGreaterEqual(corr, -1.0)
        self.assertLessEqual(corr, 1.0)


class TestRegimeDetectorThresholds(unittest.TestCase):
    """Test determine_regime threshold mapping."""

    def test_regime_normal(self):
        self.assertEqual(RegimeDetector().determine_regime(-0.8), "REGIME_NORMAL")
        self.assertEqual(RegimeDetector().determine_regime(-0.51), "REGIME_NORMAL")

    def test_regime_decoupled(self):
        self.assertEqual(RegimeDetector().determine_regime(0.3), "REGIME_DECOUPLED")
        self.assertEqual(RegimeDetector().determine_regime(-0.19), "REGIME_DECOUPLED")

    def test_regime_transition(self):
        self.assertEqual(RegimeDetector().determine_regime(-0.35), "REGIME_TRANSITION")
        self.assertEqual(RegimeDetector().determine_regime(-0.2), "REGIME_TRANSITION")
        self.assertEqual(RegimeDetector().determine_regime(-0.5), "REGIME_TRANSITION")

    def test_regime_unknown_nan(self):
        self.assertEqual(RegimeDetector().determine_regime(float("nan")), "REGIME_UNKNOWN")


class TestInnerJoinAlignment(unittest.TestCase):
    """Verify inner join drops misaligned dates and prevents NaN."""

    def test_misaligned_dates_no_nan(self):
        gold_dates = pd.date_range("2025-01-01", periods=90, freq="D")
        tips_dates = pd.date_range("2025-01-03", periods=85, freq="D")

        np.random.seed(123)
        gold = pd.Series(np.random.randn(90).cumsum() + 2000, index=gold_dates)
        tips = pd.Series(np.random.randn(85).cumsum() + 2.0, index=tips_dates)

        merged = pd.concat(
            [gold.rename("gold"), tips.rename("tips")],
            axis=1,
            join="inner",
        ).dropna()

        self.assertFalse(merged.isnull().any().any(), "Inner join should eliminate NaN")
        self.assertGreater(len(merged), 0)

        detector = RegimeDetector()
        corr = detector.calculate_correlation(gold, tips)
        self.assertFalse(math.isnan(corr), "Correlation should not be NaN with aligned data")


class TestMacroCacheGating(unittest.TestCase):
    """Verify the 24-hour cache gating in the orchestrator."""

    def test_fresh_cache_skips_update(self):
        repo = _make_in_memory_repo()
        now = int(time.time())
        repo.set_kv("last_macro_update_timestamp", str(now - 100))  # 100 seconds ago

        mock_macro = MagicMock(spec=FredMacroClient)
        orchestrator = PulseOrchestrator(
            repository_factory=lambda: repo,
            macro_client=mock_macro,
        )

        orchestrator._run_macro_regime_check(repo)

        mock_macro.fetch_10y_tips_yield.assert_not_called()
        # macro_regime should NOT be set
        self.assertIsNone(repo.get_kv("macro_regime"))

    def test_expired_cache_triggers_update(self):
        repo = _make_in_memory_repo()
        now = int(time.time())
        stale = now - MACRO_CACHE_TTL_SECONDS - 100
        repo.set_kv("last_macro_update_timestamp", str(stale))

        # Seed Gold data
        base_ts = now - 86400 * 30
        for i in range(60):
            ts = base_ts + 86400 * i
            repo.connection.execute(
                "INSERT INTO market_data (symbol, timeframe, timestamp, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("XAUUSD", "H1", ts, 2000.0 + i, 2001.0 + i, 1999.0 + i, 2000.5 + i, 100.0),
            )
        repo.connection.commit()

        tips_dates = pd.date_range(
            datetime.fromtimestamp(base_ts, tz=timezone.utc).date(),
            periods=60,
            freq="D",
        )
        tips_series = pd.Series(
            [2.0 - i * 0.01 for i in range(60)], index=tips_dates
        )
        dti = pd.DatetimeIndex(tips_series.index)
        tips_series.index = dti.tz_localize(None)

        mock_macro = MagicMock(spec=FredMacroClient)
        mock_macro.fetch_10y_tips_yield.return_value = tips_series

        orchestrator = PulseOrchestrator(
            repository_factory=lambda: repo,
            macro_client=mock_macro,
        )

        orchestrator._run_macro_regime_check(repo)

        mock_macro.fetch_10y_tips_yield.assert_called_once()

        regime = repo.get_kv("macro_regime")
        self.assertIsNotNone(regime, "macro_regime should be persisted")
        self.assertIn(regime, ["REGIME_NORMAL", "REGIME_DECOUPLED", "REGIME_TRANSITION", "REGIME_UNKNOWN"])

        corr_str = repo.get_kv("macro_tips_correlation")
        self.assertIsNotNone(corr_str)
        corr_val = float(str(corr_str))
        self.assertGreaterEqual(corr_val, -1.0)
        self.assertLessEqual(corr_val, 1.0)

        ts_str = repo.get_kv("last_macro_update_timestamp")
        self.assertIsNotNone(ts_str)
        self.assertGreater(int(str(ts_str)), stale)

    def test_no_previous_timestamp_triggers_update(self):
        repo = _make_in_memory_repo()
        now = int(time.time())

        base_ts = now - 86400 * 30
        for i in range(60):
            ts = base_ts + 86400 * i
            repo.connection.execute(
                "INSERT INTO market_data (symbol, timeframe, timestamp, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("XAUUSD", "H1", ts, 2000.0, 2001.0, 1999.0, 2000.5, 100.0),
            )
        repo.connection.commit()

        tips_dates = pd.date_range(
            datetime.fromtimestamp(base_ts, tz=timezone.utc).date(),
            periods=60,
            freq="D",
        )
        tips_series = pd.Series([2.0] * 60, index=tips_dates)
        dti = pd.DatetimeIndex(tips_series.index)
        tips_series.index = dti.tz_localize(None)

        mock_macro = MagicMock(spec=FredMacroClient)
        mock_macro.fetch_10y_tips_yield.return_value = tips_series

        orchestrator = PulseOrchestrator(
            repository_factory=lambda: repo,
            macro_client=mock_macro,
        )

        orchestrator._run_macro_regime_check(repo)

        mock_macro.fetch_10y_tips_yield.assert_called_once()
        self.assertIsNotNone(repo.get_kv("macro_regime"))


class TestFredMacroClientConfig(unittest.TestCase):
    """Verify FredMacroClient uses the correct DFII10 series name."""

    def test_dfii10_series_constant(self):
        self.assertEqual(DFII10_SERIES, "DFII10")

    def test_fetch_returns_series(self):
        """Mock pandas_datareader to verify return type."""
        sample_dates = pd.date_range("2025-01-01", periods=60, freq="D")
        sample_frame = pd.DataFrame(
            {"DFII10": [2.0 + i * 0.01 for i in range(60)]},
            index=sample_dates,
        )

        mock_pdr_data = MagicMock()
        mock_pdr_data.DataReader = MagicMock(return_value=sample_frame)

        mock_pdr_module = MagicMock()
        mock_pdr_module.data = mock_pdr_data

        with patch.dict("sys.modules", {
            "pandas_datareader": mock_pdr_module,
            "pandas_datareader.data": mock_pdr_data,
        }):
            client = FredMacroClient()
            result = client.fetch_10y_tips_yield(days=90)

        self.assertIsInstance(result, pd.Series)
        self.assertEqual(len(result), 60)


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestRegimeDetectorCorrelation))
    suite.addTests(loader.loadTestsFromTestCase(TestRegimeDetectorThresholds))
    suite.addTests(loader.loadTestsFromTestCase(TestInnerJoinAlignment))
    suite.addTests(loader.loadTestsFromTestCase(TestMacroCacheGating))
    suite.addTests(loader.loadTestsFromTestCase(TestFredMacroClientConfig))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
