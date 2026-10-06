from __future__ import annotations

from typing import Any, Optional

from config import settings
from config.instruments import get_instrument


class PermissionEngine:
    """Big-picture (macro) checks for gold setups.

    None of these checks has been proven in history tests yet, so by default a
    triggered check lowers the setup's score instead of refusing it
    (MACRO_GATES_MODE = penalty | block | off).
    """

    def macro_flags(
        self,
        setup_dict: dict[str, Any],
        macro_context: dict[str, Any],
        symbol: str = "XAUUSD",
    ) -> list[str]:
        # COT positioning, sovereign demand and gold-consensus states describe
        # the GOLD market; they must not touch BTC or FX setups.
        if not get_instrument(symbol).macro_gold_filters:
            return []

        trade_direction = str(setup_dict.get("trade_direction", "")).upper()
        macro_cot_state = self._normalize_text(macro_context.get("macro_cot_state"))
        macro_consensus_state = self._normalize_text(
            macro_context.get("macro_consensus_state")
        )
        macro_long_bias_multiplier = self._normalize_multiplier(
            macro_context.get("macro_long_bias_multiplier")
        )

        flags: list[str] = []
        if trade_direction == "LONG" and macro_cot_state == "OVERCROWDED_LONG":
            flags.append("COT Index Overcrowded Long")
        if trade_direction == "SHORT" and macro_cot_state == "CAPITULATION_SHORT":
            flags.append("COT Index Capitulation Short")
        if trade_direction == "SHORT" and macro_consensus_state == "CONTRARIAN_BULLISH":
            flags.append("Double Whammy Bullish Fundamental Shift")
        if trade_direction == "LONG" and macro_consensus_state == "CONTRARIAN_BEARISH":
            flags.append("Double Whammy Bearish Fundamental Shift")
        if trade_direction == "SHORT" and macro_long_bias_multiplier == 1.25:
            flags.append("Sovereign Demand Floor Active")
        return flags

    def is_trade_permitted(
        self,
        setup_dict: dict[str, Any],
        macro_context: dict[str, Any],
        symbol: str = "XAUUSD",
        mode: Optional[str] = None,
    ) -> tuple[bool, str]:
        active_mode = (mode or settings.MACRO_GATES_MODE or "penalty").lower()
        flags = self.macro_flags(setup_dict, macro_context, symbol)
        if flags and active_mode == "block":
            return False, f"Blocked: {flags[0]}"
        return True, "Permitted"

    def score_penalties(
        self,
        setup_dict: dict[str, Any],
        macro_context: dict[str, Any],
        symbol: str = "XAUUSD",
        mode: Optional[str] = None,
    ) -> list[tuple[int, str]]:
        active_mode = (mode or settings.MACRO_GATES_MODE or "penalty").lower()
        if active_mode != "penalty":
            return []
        points = int(settings.MACRO_GATE_PENALTY)
        return [
            (points, f"Big-picture check: {flag} (-{points}, not yet proven in history tests)")
            for flag in self.macro_flags(setup_dict, macro_context, symbol)
        ]

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
