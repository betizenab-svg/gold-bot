"""Test script for Sprint 12: Commitment of Traders (COT) Index Implementation."""
import sqlite3
import time
from unittest.mock import patch, MagicMock

from src.analysis.cot_index import CotAnalyzer
from src.ingestion.cot_client import CotClient
from src.persistence.schema import SchemaInitializer
from src.persistence.repository import Repository
from src.core.orchestrator import PulseOrchestrator

def test_cot_analyzer_normalization():
    analyzer = CotAnalyzer()
    
    # 1. Normal case: Min=100K, Max=300K, Current=250K
    # Expected: 100 * ((250-100)/(300-100)) = 100 * (150/200) = 75.0
    val1 = analyzer.calculate_index(250000.0, [100000.0, 300000.0, 250000.0])
    assert abs(val1 - 75.0) < 0.01, f"Expected 75.0, got {val1}"
    
    # 2. Max bound: Current equals Max
    # Expected: 100.0
    val2 = analyzer.calculate_index(300000.0, [100000.0, 300000.0, 250000.0])
    assert abs(val2 - 100.0) < 0.01, f"Expected 100.0, got {val2}"
    
    # 3. Min bound: Current equals Min
    # Expected: 0.0
    val3 = analyzer.calculate_index(100000.0, [100000.0, 300000.0, 250000.0])
    assert abs(val3 - 0.0) < 0.01, f"Expected 0.0, got {val3}"
    
    # 4. Zero division safety: Max == Min
    # Expected: 50.0
    val4 = analyzer.calculate_index(200000.0, [200000.0, 200000.0, 200000.0])
    assert abs(val4 - 50.0) < 0.01, f"Expected 50.0, got {val4}"

def test_cot_analyzer_state_mapping():
    analyzer = CotAnalyzer()
    
    assert analyzer.evaluate_positioning(81.0) == "OVERCROWDED_LONG"
    assert analyzer.evaluate_positioning(80.0) == "NEUTRAL"
    assert analyzer.evaluate_positioning(50.0) == "NEUTRAL"
    assert analyzer.evaluate_positioning(20.0) == "NEUTRAL"
    assert analyzer.evaluate_positioning(19.9) == "CAPITULATION_SHORT"

@patch("src.ingestion.cot_client.CotClient.fetch_historical_net_positions")
def test_cot_orchestrator_integration(mock_fetch):
    # Mock data such that Max=400K, Min=200K, Current=380K -> Index=90.0 (OVERCROWDED)
    mock_fetch.return_value = [200000.0, 400000.0, 380000.0]
    
    # Set up memory DB
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    repo = Repository(conn)
    
    # Set mock cache to force macro check
    repo.set_kv("last_macro_update_timestamp", "0")
    
    orchestrator = PulseOrchestrator(lambda: repo, None, None)
    
    import pandas as pd
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
    
    # Run the macro block directly
    orchestrator._run_macro_regime_check(repo)
    
    # Assert KV store was correctly updated
    idx_str = repo.get_kv("macro_cot_index")
    state = repo.get_kv("macro_cot_state")
    
    assert idx_str is not None, "macro_cot_index should be saved in kv_store"
    assert "90.0" in idx_str, f"Expected index ~90.00, got {idx_str}"
    
    assert state is not None, "macro_cot_state should be saved in kv_store"
    assert state == "OVERCROWDED_LONG", f"Expected OVERCROWDED_LONG, got {state}"

if __name__ == "__main__":
    print("============================================================")
    print("Sprint 12: COT Index Verification")
    print("============================================================")

    test_cot_analyzer_normalization()
    print("[1/3] COT math bounds & zero-division safety ... PASSED")
    
    test_cot_analyzer_state_mapping()
    print("[2/3] COT threshold state mapping ... PASSED")
    
    test_cot_orchestrator_integration()
    print("[3/3] Orchestrator KV persistence integration ... PASSED")

    print("============================================================")
    print("Sprint 12 Verification Successfully Completed.")
    print("============================================================")
