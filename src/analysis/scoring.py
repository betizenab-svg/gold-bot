from __future__ import annotations

from typing import Any, Dict, Optional


class ScoringEngine:
    """Stateless confluence scoring for potential trade setups."""

    def score_macro_bias(self, trade_direction: str, macro_bias: str) -> int:
        direction = trade_direction.upper()
        bias = macro_bias.upper()

        if bias == "NEUTRAL":
            return 10

        if (direction == "LONG" and bias == "BIAS_LONG") or (
            direction == "SHORT" and bias == "BIAS_SHORT"
        ):
            return 25

        return 0

    def score_trend_alignment(self, trade_direction: str, current_structure: str) -> int:
        direction = trade_direction.upper()
        structure = current_structure.upper()

        if (direction == "LONG" and structure == "BULLISH") or (
            direction == "SHORT" and structure == "BEARISH"
        ):
            return 25

        return 0

    def score_zone_quality(self, zone_dict: Optional[Dict[str, Any]]) -> int:
        if zone_dict is None:
            return 0

        status = str(zone_dict.get("status", "")).upper()

        if status in {"ACTIVE", "UNMITIGATED"}:
            return 20
        if status == "MITIGATED":
            return 10

        return 0

    def score_liquidity(self, has_recent_sweep: bool) -> int:
        return 30 if has_recent_sweep else 0

    def calculate_total_score(
        self,
        trade_direction: str,
        macro_bias: str,
        current_structure: str,
        zone_dict: Optional[Dict[str, Any]],
        has_recent_sweep: bool,
    ) -> int:
        total = (
            self.score_macro_bias(trade_direction, macro_bias)
            + self.score_trend_alignment(trade_direction, current_structure)
            + self.score_zone_quality(zone_dict)
            + self.score_liquidity(has_recent_sweep)
        )

        return int(max(0, min(100, total)))

    def classify_score(self, score: int) -> str:
        if score < 50:
            return "REJECTED"
        if score <= 74:
            return "WATCHLIST"
        return "ACTIONABLE"
