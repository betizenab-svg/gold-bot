import os
import pandas as pd
from unittest.mock import patch, MagicMock
from src.analysis.fsr_engine import FSREngine
from src.core.orchestrator import PulseOrchestrator

def test_slope_calculation():
    engine = FSREngine()
    
    # Dummy price_series strongly trending upward
    price_series_up = [float(i) for i in range(1, 21)]
    # Dummy surprise_series completely flat
    surprise_series_flat = [0.0] * 20
    
    fsr_up = engine.calculate_fsr(price_series_up, surprise_series_flat)
    assert fsr_up > 0.0, f"Expected positive float > 0.0, got {fsr_up}"
    
    # Reverse the price_series
    price_series_down = list(reversed(price_series_up))
    fsr_down = engine.calculate_fsr(price_series_down, surprise_series_flat)
    assert fsr_down < 0.0, f"Expected negative float < 0.0, got {fsr_down}"

def test_state_evaluation():
    engine = FSREngine()
    assert engine.evaluate_fsr_state(0.6) == 'HIGH_MOMENTUM'
    assert engine.evaluate_fsr_state(-0.8) == 'MEAN_REVERSION'
    assert engine.evaluate_fsr_state(0.1) == 'EQUILIBRIUM'

@patch.dict(os.environ, {"MOCK_INGESTION": "1", "MOCK_CANDLES_PER_RUN": "0"})
def test_orchestrator_integration():
    repo_mock = MagicMock()
    # Mock the 24-hour time check to force a macro update (by returning 0 for "last_macro_update_timestamp")
    repo_mock.get_kv.return_value = "0"
    
    orchestrator = PulseOrchestrator(repository_factory=lambda: repo_mock)
    
    # Provide dummy arrays for both price and surprise series...
    # (Surprise series is hardcoded in orchestrator, so we provide gold series to trigger calculating)
    mock_series = pd.Series([1000.0 + i for i in range(20)])
    orchestrator._fetch_gold_daily_closes = MagicMock(return_value=mock_series)
    orchestrator._fetch_dxy_daily_closes = MagicMock(return_value=pd.Series(dtype=float))
    
    # Mock out other components to isolate FSR text
    orchestrator.macro_client = MagicMock()
    orchestrator.regime_detector = MagicMock()
    orchestrator.sovereign_proxy = MagicMock()
    orchestrator.crisis_detector = MagicMock()
    orchestrator._mock_client = MagicMock()
    
    # Run the PulseOrchestrator execution cycle
    orchestrator.run()
    
    # Assert repository.set_kv was called with 'macro_fsr_value' and 'macro_fsr_state'.
    calls = repo_mock.set_kv.call_args_list
    keys = [call.args[0] for call in calls]
    assert 'macro_fsr_value' in keys, "set_kv was not called with 'macro_fsr_value'"
    assert 'macro_fsr_state' in keys, "set_kv was not called with 'macro_fsr_state'"

if __name__ == "__main__":
    test_slope_calculation()
    test_state_evaluation()
    test_orchestrator_integration()
    print("Sprint 14 FSR Engine Verified")
