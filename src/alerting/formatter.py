from __future__ import annotations

from typing import Any


class SignalFormatter:
    """Format outbound Telegram messages for initial trade alerts and lifecycle replies."""

    def format_initial_signal(self, signal_obj: Any) -> str:
        entry_price = self._get_numeric(signal_obj, "entry_price", "entry")
        sl_price = self._get_numeric(signal_obj, "sl_price", "sl")
        tp1_price = self._get_numeric(signal_obj, "tp1_price", "tp1")
        tp2_price = self._get_numeric(signal_obj, "tp2_price", "tp2")
        lines = [
            "Market execution/pending order",
            f"entry @ {entry_price:.2f}",
            f"sl @ {sl_price:.2f}",
            f"tp 1 @ {tp1_price:.2f}",
            f"tp 2 @ {tp2_price:.2f}",
        ]
        return "\n".join(lines)

    def format_trade_reasoning(self, signal_obj: Any, lot_size_table: str) -> str:
        symbol = self._get_value(signal_obj, "symbol", default="XAUUSD")
        direction = self._get_value(signal_obj, "signal_type", "type", default="UNKNOWN")
        score = self._get_value(signal_obj, "score", default="N/A")
        reasoning = self._get_value(signal_obj, "reasoning", default="No reasoning provided.")
        return (
            "Trade reasoning\n"
            f"symbol: {symbol}\n"
            f"direction: {direction}\n"
            f"score: {score}\n"
            f"detail: {reasoning}\n\n"
            f"{lot_size_table}"
        )

    def format_lifecycle_update(self, update_type: str, reason: str) -> tuple[str, str]:
        normalized_type = update_type.upper()
        if normalized_type == "TP1_SMASH":
            alert_message = "TP 1 SMASHED!"
        elif normalized_type == "TP2_SMASH":
            alert_message = "TP 2 SMASHED!"
        elif normalized_type == "SL_HIT":
            alert_message = "SL HIT!"
        else:
            raise ValueError(f"Unsupported lifecycle update type: {update_type}")

        explanation_message = f"Reason: {reason}"
        return alert_message, explanation_message

    @staticmethod
    def _get_numeric(signal_obj: Any, *keys: str) -> float:
        value = SignalFormatter._get_value(signal_obj, *keys)
        return round(float(value), 2)

    @staticmethod
    def _get_value(signal_obj: Any, *keys: str, default: Any = None) -> Any:
        for key in keys:
            if isinstance(signal_obj, dict) and key in signal_obj:
                return signal_obj[key]
            if hasattr(signal_obj, key):
                return getattr(signal_obj, key)
        if default is not None:
            return default
        joined = ", ".join(keys)
        raise AttributeError(f"signal object is missing required fields: {joined}")
