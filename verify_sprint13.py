"""Test script for Sprint 13: Consensus Variance (Surprise Factor)."""
import sqlite3
from unittest.mock import patch, MagicMock

import pandas as pd

from src.analysis.consensus import SurpriseFactorEngine
from src.ingestion.calendar_client import EconomicCalendarClient
from src.persistence.schema import SchemaInitializer
from src.persistence.repository import Repository
from src.core.orchestrator import PulseOrchestrator


def test_surprise_factor_formula():
    engine = SurpriseFactorEngine()

    # Normal case: (actual - forecast) / sigma = (-50 - 180) / 45 = -5.11
    sf1 = engine.calculate_surprise_factor(-50.0, 180.0, 45.0)
    assert abs(sf1 - (-230.0 / 45.0)) < 0.01, f"Expected ~-5.11, got {sf1}"

    # Zero sigma: should return 0.0
    sf2 = engine.calculate_surprise_factor(100.0, 50.0, 0.0)
    assert sf2 == 0.0, f"Expected 0.0, got {sf2}"

    # Positive surprise: (0.5 - 0.3) / 0.1 = 2.0
    sf3 = engine.calculate_surprise_factor(0.5, 0.3, 0.1)
    assert abs(sf3 - 2.0) < 0.01, f"Expected 2.0, got {sf3}"


def test_double_whammy_logic():
    engine = SurpriseFactorEngine()

    # CONTRARIAN_BULLISH: forecast > 0, actual < 0, |surprise| >= 2.0
    event_bull = {
        "event_name": "NFP",
        "forecast": 180.0,
        "actual": -50.0,
        "historical_sigma": 45.0,
        "usd_impact_direction": 1,
    }
    assert engine.evaluate_double_whammy(event_bull) == "CONTRARIAN_BULLISH"

    # CONTRARIAN_BEARISH: forecast < 0, actual > 0, |surprise| >= 2.0
    event_bear = {
        "event_name": "FOMC",
        "forecast": -0.25,
        "actual": 0.25,
        "historical_sigma": 0.15,
        "usd_impact_direction": -1,
    }
    assert engine.evaluate_double_whammy(event_bear) == "CONTRARIAN_BEARISH"

    # NEUTRAL: same direction, surprise not inverted
    event_neutral = {
        "event_name": "CPI",
        "forecast": 0.3,
        "actual": 0.5,
        "historical_sigma": 0.1,
        "usd_impact_direction": 1,
    }
    assert engine.evaluate_double_whammy(event_neutral) == "NEUTRAL"

    # NEUTRAL: inverted but magnitude below threshold
    event_small = {
        "event_name": "GDP",
        "forecast": 1.0,
        "actual": -0.5,
        "historical_sigma": 100.0,
        "usd_impact_direction": 1,
    }
    assert engine.evaluate_double_whammy(event_small) == "NEUTRAL"


def test_calendar_client_types():
    client = EconomicCalendarClient()
    events = client.fetch_latest_events()
    assert isinstance(events, list), "Expected a list"
    assert len(events) > 0, "Expected at least one event"
    for ev in events:
        assert "event_name" in ev
        assert "forecast" in ev
        assert "actual" in ev
        assert "historical_sigma" in ev
        assert "usd_impact_direction" in ev


def test_orchestrator_integration():
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    repo = Repository(conn)
    repo.set_kv("last_macro_update_timestamp", "0")

    orchestrator = PulseOrchestrator(lambda: repo, None, None)

    mock_series = pd.Series([1.0])
    orchestrator._fetch_gold_daily_closes = MagicMock(return_value=mock_series)
    orchestrator._fetch_dxy_daily_closes = MagicMock(return_value=mock_series)
    orchestrator.macro_client = MagicMock()
    orchestrator.macro_client.fetch_10y_tips_yield.return_value = mock_series
    orchestrator.regime_detector = MagicMock()
    orchestrator.regime_detector.calculate_correlation.return_value = 0.5
    orchestrator.regime_detector.determine_regime.return_value = "NORMAL"
    orchestrator.sovereign_proxy = MagicMock()
    orchestrator.sovereign_proxy.get_net_purchases.return_value = 0.0
    orchestrator.sovereign_proxy.calculate_multiplier.return_value = 1.0
    orchestrator.crisis_detector = MagicMock()
    orchestrator.crisis_detector.calculate_dxy_correlation.return_value = 0.5
    orchestrator.crisis_detector.evaluate_crisis_mode.return_value = False

    orchestrator._run_macro_regime_check(repo)

    sf_str = repo.get_kv("macro_surprise_factor")
    state = repo.get_kv("macro_consensus_state")

    assert sf_str is not None, "macro_surprise_factor should be saved"
    sf_val = float(sf_str)
    assert sf_val > 0.0, f"Expected positive surprise factor, got {sf_val}"

    assert state is not None, "macro_consensus_state should be saved"
    assert state in (
        "CONTRARIAN_BULLISH",
        "CONTRARIAN_BEARISH",
        "NEUTRAL",
    ), f"Unexpected state: {state}"


if __name__ == "__main__":
    print("============================================================")
    print("Sprint 13: Consensus Variance (Surprise Factor) Verification")
    print("============================================================")

    test_surprise_factor_formula()
    print("[1/4] Surprise factor formula & zero-division safety ... PASSED")

    test_double_whammy_logic()
    print("[2/4] Double Whammy contrarian state mapping ... PASSED")

    test_calendar_client_types()
    print("[3/4] EconomicCalendarClient return types ... PASSED")

    test_orchestrator_integration()
    print("[4/4] Orchestrator KV persistence integration ... PASSED")

    print("============================================================")
    print("Sprint 13 Verification Successfully Completed.")
    print("============================================================")
