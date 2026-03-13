import sys
from pathlib import Path
from typing import Any, Dict

# Ensure project root is in path
workspace = Path(__file__).resolve().parent
if str(workspace) not in sys.path:
    sys.path.insert(0, str(workspace))

from config.settings import SURPRISE_FACTOR_THRESHOLD
from src.analysis.consensus import SurpriseFactorEngine
from src.core.orchestrator import PulseOrchestrator
from src.ingestion.calendar_client import EconomicCalendarClient
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from config.database import get_connection

def main() -> int:
    try:
        engine = SurpriseFactorEngine()
        
        print("Testing Surprise Factor Math...")
        # Assert calculate_surprise_factor(actual=1.0, forecast=5.0, sigma=2.0) returns -2.0.
        sf1 = engine.calculate_surprise_factor(actual=1.0, forecast=5.0, sigma=2.0)
        assert sf1 == -2.0, f"Expected -2.0, got {sf1}"
        
        # Assert calculate_surprise_factor(actual=5.0, forecast=5.0, sigma=0.0) returns 0.0 (zero division check).
        sf2 = engine.calculate_surprise_factor(actual=5.0, forecast=5.0, sigma=0.0)
        assert sf2 == 0.0, f"Expected 0.0, got {sf2}"
        print("Surprise Factor Math Passed")

        print("\nTesting Double Whammy Logic...")
        # Scenario A (Contrarian Bullish): predict 3.0, actual -2.0, sigma 2.0
        event_a: Dict[str, Any] = {
            "forecast": 3.0,
            "actual": -2.0,
            "historical_sigma": 2.0,
            "usd_impact_direction": 1
        }
        res_a = engine.evaluate_double_whammy(event_a)
        assert res_a == "CONTRARIAN_BULLISH", f"Expected CONTRARIAN_BULLISH, got {res_a}"
        
        # Scenario B (Contrarian Bearish): predict -2.0, actual 3.0, sigma 2.0
        event_b: Dict[str, Any] = {
            "forecast": -2.0,
            "actual": 3.0,
            "historical_sigma": 2.0,
            "usd_impact_direction": 1
        }
        res_b = engine.evaluate_double_whammy(event_b)
        assert res_b == "CONTRARIAN_BEARISH", f"Expected CONTRARIAN_BEARISH, got {res_b}"
        
        # Scenario C (Neutral / Below Threshold): abs((-0.5 - 1.0)/2.0) < 2.0 -> NEUTRAL
        event_c: Dict[str, Any] = {
            "forecast": 1.0,
            "actual": -0.5,
            "historical_sigma": 2.0,
            "usd_impact_direction": 1
        }
        res_c = engine.evaluate_double_whammy(event_c)
        assert res_c == "NEUTRAL", f"Expected NEUTRAL, got {res_c}"
        print("Double Whammy Logic Passed")

        print("\nTesting Orchestrator Integration...")
        
        import sqlite3
        conn = sqlite3.connect(":memory:")
        SchemaInitializer(conn).initialize()
        repository = Repository(conn)
        
        # Force the 24-hour block to run
        repository.set_kv("last_macro_update", "0")
        
        class MockCalendarClient(EconomicCalendarClient):
            def fetch_latest_events(self) -> list[Dict[str, Any]]:
                # Return Scenario A
                return [event_a]
                
        # Inject our mock client by replacing it within orchestrator
        original_client = EconomicCalendarClient
        
        from unittest.mock import MagicMock
        import pandas as pd
        mock_series = pd.Series([1.0])
        
        class TestOrchestrator(PulseOrchestrator):
            def _run_macro_regime_check(self, repository: Repository) -> None:
                import src.core.orchestrator as orch_module
                setattr(orch_module, "EconomicCalendarClient", MockCalendarClient)
                try:
                    super()._run_macro_regime_check(repository)
                finally:
                    setattr(orch_module, "EconomicCalendarClient", original_client)

        try:
            orchestrator = TestOrchestrator(
                repository_factory=lambda: repository
            )
            
            # Mock dependencies to prevent early returns
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
            
            # Run the specific macro regime check
            orchestrator._run_macro_regime_check(repository)
            
            state = repository.get_kv("macro_consensus_state")
            assert state == "CONTRARIAN_BULLISH", f"Expected macro_consensus_state='CONTRARIAN_BULLISH', got {state}"
            print("Orchestrator Integration Passed")
            
        finally:
            conn.close()

        print("\nSprint 13 Consensus Variance Verified")
        return 0

    except AssertionError as e:
        print(f"Assertion Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
