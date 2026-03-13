import os
import pandas as pd
from unittest.mock import patch, MagicMock

from src.analysis.bias_engine import MacroBiasAggregator
from src.core.orchestrator import PulseOrchestrator

def test_scoring_logic_bullish():
    repo_mock = MagicMock()
    def mock_get_kv(key):
        if key == "macro_crisis_mode": return "1"
        if key == "macro_cot_state": return "NEUTRAL"
        if key == "macro_consensus_state": return "CONTRARIAN_BULLISH"
        if key == "macro_long_bias_multiplier": return "1.25"
        return None
    repo_mock.get_kv.side_effect = mock_get_kv
    
    engine = MacroBiasAggregator()
    res = engine.calculate_bias(repo_mock)
    
    # 25 (Crisis) + 30 (Consensus) = 55 Base Score
    # 55 * 1.25 = 68.75. The orchestrator requirements dictate saving an integer score.
    # Therefore, 68.75 rounds to 69.
    assert res['score'] == 69, f"Expected final score of 69 (rounded from 68.75), got {res['score']}"
    assert res['bias'] == 'BIAS_LONG', f"Expected BIAS_LONG, got {res['bias']}"

def test_scoring_logic_bearish():
    repo_mock = MagicMock()
    def mock_get_kv(key):
        if key == "macro_crisis_mode": return "0"
        if key == "macro_cot_state": return "OVERCROWDED_LONG"
        if key == "macro_consensus_state": return "CONTRARIAN_BEARISH"
        if key == "macro_long_bias_multiplier": return "1.25"
        return None
    repo_mock.get_kv.side_effect = mock_get_kv
    
    engine = MacroBiasAggregator()
    res = engine.calculate_bias(repo_mock)
    
    # -20 (COT) + -30 (Consensus) = -50 Base Score
    # Multiplier should NOT apply to negative scores
    assert res['score'] == -50, f"Expected final score of -50, got {res['score']}"
    assert res['bias'] == 'BIAS_SHORT', f"Expected BIAS_SHORT, got {res['bias']}"

def test_graceful_degradation():
    repo_mock = MagicMock()
    repo_mock.get_kv.return_value = None
    
    engine = MacroBiasAggregator()
    res = engine.calculate_bias(repo_mock)
    
    assert res['score'] == 0, f"Expected 0, got {res['score']}"
    assert res['bias'] == 'BIAS_NEUTRAL', f"Expected BIAS_NEUTRAL, got {res['bias']}"

@patch.dict(os.environ, {"MOCK_INGESTION": "1", "MOCK_CANDLES_PER_RUN": "0"})
def test_orchestrator_integration():
    repo_mock = MagicMock()
    # Mock the 24-hour time check to force a macro update
    repo_mock.get_kv.return_value = "0"
    
    orchestrator = PulseOrchestrator(repository_factory=lambda: repo_mock)
    
    # Provide dummy arrays for both price and surprise series (trigger logic correctly)
    mock_series = pd.Series([1000.0 + i for i in range(20)])
    orchestrator._fetch_gold_daily_closes = MagicMock(return_value=mock_series)
    orchestrator._fetch_dxy_daily_closes = MagicMock(return_value=mock_series)
    
    orchestrator.macro_client = MagicMock()
    orchestrator.regime_detector = MagicMock()
    orchestrator.sovereign_proxy = MagicMock()
    orchestrator.crisis_detector = MagicMock()
    orchestrator._mock_client = MagicMock()
    
    # Run the PulseOrchestrator execution cycle
    orchestrator.run()
    
    # Assert repository.set_kv was called with 'global_macro_score' and 'global_macro_bias'
    calls = repo_mock.set_kv.call_args_list
    keys = [call.args[0] for call in calls]
    assert 'global_macro_score' in keys, "set_kv was not called with 'global_macro_score'"
    assert 'global_macro_bias' in keys, "set_kv was not called with 'global_macro_bias'"

if __name__ == "__main__":
    test_scoring_logic_bullish()
    test_scoring_logic_bearish()
    test_graceful_degradation()
    test_orchestrator_integration()
    print("Sprint 15 Macro-Bias Aggregation Verified")
