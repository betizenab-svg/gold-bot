"""Test script for Sprint 14: Fundamental Shift Rate (FSR) Engine."""
import sqlite3
from unittest.mock import patch, MagicMock

import pandas as pd

from config.settings import FSR_LOOKBACK_PERIOD
from src.analysis.fsr_engine import FSREngine
from src.persistence.schema import SchemaInitializer
from src.persistence.repository import Repository
from src.core.orchestrator import PulseOrchestrator


def test_fsr_engine_logic():
    engine = FSREngine()
    
    # 1. Constant positive slope for price
    price_series = [100.0 + i * 2.5 for i in range(FSR_LOOKBACK_PERIOD)]
    # Constant positive slope for surprise
    surprise_series = [0.1 * i for i in range(FSR_LOOKBACK_PERIOD)]
    
    fsr_value = engine.calculate_fsr(price_series, surprise_series)
    assert isinstance(fsr_value, float), "Expected float output from calculate_fsr"
    
    # Check states correctly evaluate
    assert engine.evaluate_fsr_state(1.0) == "HIGH_MOMENTUM"
    assert engine.evaluate_fsr_state(-1.0) == "MEAN_REVERSION"
    assert engine.evaluate_fsr_state(0.0) == "EQUILIBRIUM"


def test_orchestrator_integration():
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    repo = Repository(conn)
    repo.set_kv("last_macro_update_timestamp", "0")

    orchestrator = PulseOrchestrator(lambda: repo, None, None)

    # Mock 20 days of data for gold
    mock_series_list = [1000.0 + i for i in range(FSR_LOOKBACK_PERIOD)]
    mock_series = pd.Series(mock_series_list)
    
    orchestrator._fetch_gold_daily_closes = MagicMock(return_value=mock_series)
    orchestrator._fetch_dxy_daily_closes = MagicMock(return_value=pd.Series(dtype=float))
    
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

    fsr_val_str = repo.get_kv("macro_fsr_value")
    state = repo.get_kv("macro_fsr_state")

    assert fsr_val_str is not None, "macro_fsr_value should be saved"
    fsr_val = float(fsr_val_str)
    
    assert state is not None, "macro_fsr_state should be saved"
    assert state in (
        "HIGH_MOMENTUM",
        "MEAN_REVERSION",
        "EQUILIBRIUM",
    ), f"Unexpected state: {state}"


if __name__ == "__main__":
    print("============================================================")
    print("Sprint 14: Fundamental Shift Rate (FSR) Engine Verification")
    print("============================================================")

    test_fsr_engine_logic()
    print("[1/2] FSR Engine Logic & State Evaluation ... PASSED")

    test_orchestrator_integration()
    print("[2/2] Orchestrator KV persistence integration ... PASSED")

    print("============================================================")
    print("Sprint 14 Verification Successfully Completed.")
    print("============================================================")
