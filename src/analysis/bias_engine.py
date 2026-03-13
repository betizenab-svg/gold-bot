from config.settings import (
    SCORE_CRISIS_MODE,
    SCORE_COT_BULLISH,
    SCORE_COT_BEARISH,
    SCORE_CONSENSUS_BULLISH,
    SCORE_CONSENSUS_BEARISH,
    BIAS_LONG_THRESHOLD,
    BIAS_SHORT_THRESHOLD,
)
from src.persistence.repository import Repository


class MacroBiasAggregator:
    """
    Synthesizes discrete macro indicators into a weighted global fundamental bias
    to govern technical signals.
    """

    def _fetch_macro_state(self, repository: Repository) -> dict:
        """
        Retrieves the latest keys from kv_store, defaulting missing elements
        to neutral/inactive states for resilient summation.
        """
        def get_str(key: str, default: str) -> str:
            val = repository.get_kv(key)
            return str(val) if val is not None else default

        def get_float(key: str, default: float) -> float:
            val = repository.get_kv(key)
            if val is None:
                return default
            try:
                return float(val)
            except ValueError:
                return default

        # Fetch boolean representing crisis mode safely
        crisis_raw = get_str("macro_crisis_mode", "0")
        crisis_mode = crisis_raw == "1"

        return {
            "regime": get_str("macro_regime", "NORMAL"),
            "multiplier": get_float("macro_long_bias_multiplier", 1.0),
            "crisis_mode": crisis_mode,
            "cot_state": get_str("macro_cot_state", "NEUTRAL"),
            "consensus_state": get_str("macro_consensus_state", "NEUTRAL"),
            "fsr_state": get_str("macro_fsr_state", "EQUILIBRIUM"),
        }

    def calculate_bias(self, repository: Repository) -> dict:
        """
        Evaluates the aggregated base_score, applying the bullish sovereign multiplier 
        only to positive valuations, and returns the final score vs bias mapping.
        """
        state = self._fetch_macro_state(repository)
        base_score = 0.0

        # === 1. Crisis Logic ===
        if state["crisis_mode"]:
            base_score += SCORE_CRISIS_MODE

        # === 2. COT Positioning Logic ===
        cot = state["cot_state"]
        if cot == "CAPITULATION_SHORT":
            base_score += SCORE_COT_BULLISH
        elif cot == "OVERCROWDED_LONG":
            base_score += SCORE_COT_BEARISH

        # === 3. Consensus Surprise Logic ===
        consensus = state["consensus_state"]
        if consensus == "CONTRARIAN_BULLISH":
            base_score += SCORE_CONSENSUS_BULLISH
        elif consensus == "CONTRARIAN_BEARISH":
            base_score += SCORE_CONSENSUS_BEARISH

        # === 4. Regime & Sovereign Logic ===
        # Ensure multiplier is cast safely
        mult = float(state["multiplier"])
        
        # sovereign multiplier exclusively scales structural BULLISH conviction
        if base_score > 0:
            final_score = int(round(base_score * mult))
        else:
            final_score = int(round(base_score))

        # === 5. Global Bias Determination ===
        bias = "BIAS_NEUTRAL"
        if final_score >= BIAS_LONG_THRESHOLD:
            bias = "BIAS_LONG"
        elif final_score <= BIAS_SHORT_THRESHOLD:
            bias = "BIAS_SHORT"

        return {
            "score": final_score,
            "bias": bias
        }
