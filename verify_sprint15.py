"""Test script for Sprint 15: Macro-Bias Aggregation."""
import sqlite3
from unittest.mock import MagicMock

from config.settings import (
    SCORE_CRISIS_MODE,
    SCORE_COT_BULLISH,
    SCORE_COT_BEARISH,
    SCORE_CONSENSUS_BULLISH,
    SCORE_CONSENSUS_BEARISH,
)
from src.analysis.bias_engine import MacroBiasAggregator
from src.persistence.schema import SchemaInitializer
from src.persistence.repository import Repository
from src.core.orchestrator import PulseOrchestrator


def test_aggregator_logic():
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    repo = Repository(conn)
    
    # 1. Test Neutral Fallback and Thresholds
    # Missing all keys should return BIAS_NEUTRAL and 0 score
    engine = MacroBiasAggregator()
    res1 = engine.calculate_bias(repo)
    assert res1["score"] == 0, f"Expected 0 score, got {res1['score']}"
    assert res1["bias"] == "BIAS_NEUTRAL"

    # 2. Test positive accumulation (BULLISH)
    repo.set_kv("macro_crisis_mode", "1")
    repo.set_kv("macro_cot_state", "CAPITULATION_SHORT") # Bullish
    repo.set_kv("macro_consensus_state", "CONTRARIAN_BULLISH")
    repo.set_kv("macro_long_bias_multiplier", "1.5") # inflate the score
    
    res2 = engine.calculate_bias(repo)
    base = SCORE_CRISIS_MODE + SCORE_COT_BULLISH + SCORE_CONSENSUS_BULLISH
    expected_bull = int(round(base * 1.5))
    assert res2["score"] == expected_bull, f"Expected {expected_bull}, got {res2['score']}"
    assert res2["bias"] == "BIAS_LONG", f"Expected BIAS_LONG, got {res2['bias']}"

    # 3. Test negative accumulation (BEARISH)
    # The multiplier should NOT inflate negative base scores.
    repo.set_kv("macro_crisis_mode", "0")
    repo.set_kv("macro_cot_state", "OVERCROWDED_LONG") # Bearish
    repo.set_kv("macro_consensus_state", "CONTRARIAN_BEARISH") # Bearish
    
    res3 = engine.calculate_bias(repo)
    expected_bear = int(round(SCORE_COT_BEARISH + SCORE_CONSENSUS_BEARISH))
    assert res3["score"] == expected_bear, f"Expected {expected_bear}, got {res3['score']}"
    assert res3["bias"] == "BIAS_SHORT", f"Expected BIAS_SHORT, got {res3['bias']}"

def test_orchestrator_integration():
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    repo = Repository(conn)
    repo.set_kv("last_macro_update_timestamp", "0")

    orchestrator = PulseOrchestrator(lambda: repo, None, None)
    
    # Mock all internal sequences up to the aggregator to prevent exceptions
    mock_series = MagicMock()
    mock_series.empty = False
    orchestrator._fetch_gold_daily_closes = MagicMock(return_value=mock_series)
    orchestrator._fetch_dxy_daily_closes = MagicMock(return_value=mock_series)
    orchestrator.macro_client = MagicMock()
    orchestrator.regime_detector = MagicMock()
    orchestrator.sovereign_proxy = MagicMock()
    orchestrator.crisis_detector = MagicMock()
    # Execute the gated wrapper function
    orchestrator._run_macro_regime_check(repo)
    
    score_str = repo.get_kv("global_macro_score")
    bias_str = repo.get_kv("global_macro_bias")

    assert score_str is not None, "global_macro_score should be saved"
    assert bias_str is not None, "global_macro_bias should be saved"
    assert bias_str == "BIAS_NEUTRAL", f"Expected fallback behavior BIAS_NEUTRAL, got {bias_str}"


if __name__ == "__main__":
    print("============================================================")
    print("Sprint 15: Macro-Bias Aggregation Engine Verification")
    print("============================================================")

    test_aggregator_logic()
    print("[1/2] Macro-Bias Engine Dictionary Point Summations ... PASSED")

    test_orchestrator_integration()
    print("[2/2] Orchestrator end-of-chain KV persistence ... PASSED")

    print("============================================================")
    print("Sprint 15 Verification Successfully Completed.")
    print("============================================================")
