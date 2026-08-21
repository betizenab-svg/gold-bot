from __future__ import annotations

from typing import Any

from config.instruments import get_instrument


class PermissionEngine:
    """Absolute macro-structural gatekeeper for technical setups."""

    def is_trade_permitted(
        self,
        setup_dict: dict[str, Any],
        macro_context: dict[str, Any],
        symbol: str = "XAUUSD",
    ) -> tuple[bool, str]:
        # COT positioning, sovereign demand and gold-consensus states describe
        # the GOLD market; they must not veto BTC or FX setups.
        if not get_instrument(symbol).macro_gold_filters:
            return True, "Permitted"

        trade_direction = str(setup_dict.get("trade_direction", "")).upper()
        macro_cot_state = self._normalize_text(macro_context.get("macro_cot_state"))
        macro_consensus_state = self._normalize_text(
            macro_context.get("macro_consensus_state")
        )
        macro_long_bias_multiplier = self._normalize_multiplier(
            macro_context.get("macro_long_bias_multiplier")
        )

        if trade_direction == "LONG" and macro_cot_state == "OVERCROWDED_LONG":
            return False, "Blocked: COT Index Overcrowded Long"
        if trade_direction == "SHORT" and macro_cot_state == "CAPITULATION_SHORT":
            return False, "Blocked: COT Index Capitulation Short"

        if (
            trade_direction == "SHORT"
            and macro_consensus_state == "CONTRARIAN_BULLISH"
        ):
            return False, "Blocked: Double Whammy Bullish Fundamental Shift"
        if (
            trade_direction == "LONG"
            and macro_consensus_state == "CONTRARIAN_BEARISH"
        ):
            return False, "Blocked: Double Whammy Bearish Fundamental Shift"

        if trade_direction == "SHORT" and macro_long_bias_multiplier == 1.25:
            return False, "Blocked: Sovereign Demand Floor Active"

        return True, "Permitted"

    @staticmethod
    def _normalize_text(raw_value: Any) -> str:
        if raw_value is None:
            return ""
        return str(raw_value).strip().upper()

    @staticmethod
    def _normalize_multiplier(raw_value: Any) -> float | None:
        if raw_value is None:
            return None
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None
