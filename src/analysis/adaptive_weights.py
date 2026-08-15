from __future__ import annotations

from typing import Any


class AdaptiveWeightEngine:
    """Learn per-strategy edge from realized outcomes and scale confluence.

    Weighting uses rolling EXPECTANCY in R (not win rate): a trend strategy
    with 40% wins at +2.25R beats a 60% winner that nets nothing. Weight
    authority phases in with sample size (10 closed trades minimum, full
    authority at 20) so a small streak can never distort scoring.
    """

    MIN_SAMPLES = 10
    FULL_AUTHORITY_SAMPLES = 20
    LOOKBACK = 30
    FLOOR = 0.75
    CEILING = 1.15

    # Realized R per outcome given TP1=1.5R (half off) and TP2=3R structure.
    OUTCOME_R = {
        "CLOSED_TP2": 2.25,
        "CLOSED_BE": 0.75,
        "CLOSED_SL": -1.0,
    }

    def calculate_weight(self, repository: Any, strategy: str | None) -> dict[str, Any]:
        neutral = {"weight": 1.0, "note": "Adaptive weight: neutral (insufficient data)"}
        if not strategy:
            return neutral

        try:
            outcomes = repository.get_strategy_outcomes(str(strategy), self.LOOKBACK)
        except Exception:
            return neutral

        if not isinstance(outcomes, list):
            return neutral

        r_values = [
            self.OUTCOME_R[str(status).upper()]
            for status in outcomes
            if str(status).upper() in self.OUTCOME_R
        ]
        samples = len(r_values)
        if samples < self.MIN_SAMPLES:
            return neutral

        expectancy = sum(r_values) / samples
        # Map expectancy [-0.5R .. +1.0R] onto the weight band.
        normalized = max(-0.5, min(1.0, expectancy))
        if normalized >= 0:
            raw_weight = 1.0 + (self.CEILING - 1.0) * (normalized / 1.0)
        else:
            raw_weight = 1.0 + (1.0 - self.FLOOR) * (normalized / 0.5)

        # Phase in authority between MIN_SAMPLES and FULL_AUTHORITY_SAMPLES.
        authority = min(1.0, samples / float(self.FULL_AUTHORITY_SAMPLES))
        weight = 1.0 + (raw_weight - 1.0) * authority
        weight = max(self.FLOOR, min(self.CEILING, weight))

        return {
            "weight": round(float(weight), 3),
            "note": (
                f"Adaptive weight x{weight:.2f} for {strategy} "
                f"(expectancy {expectancy:+.2f}R over {samples} closed)"
            ),
        }
