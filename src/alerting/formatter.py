from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any


class SignalFormatter:
    """Format outbound Telegram messages for initial trade alerts and lifecycle replies."""

    LOT_SIZE_TABLE_MARKER = "[LOT_SIZE_TABLE]"

    def format_initial_signal(self, signal_obj: Any) -> str:
        symbol = self._escape(self._get_value(signal_obj, "symbol", default="XAUUSD"))
        direction = self._escape(
            str(self._get_value(signal_obj, "signal_type", "type", default="UNKNOWN")).upper()
        )
        entry_price = self._get_numeric(signal_obj, "entry_price", "entry")
        sl_price = self._get_numeric(signal_obj, "sl_price", "sl")
        tp1_price = self._get_numeric(signal_obj, "tp1_price", "tp1")
        tp2_price = self._get_numeric(signal_obj, "tp2_price", "tp2")
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        return (
            "🚨 <b>Signal Alert</b>\n"
            f"🟡 <b>Status:</b> market execution/pending order\n"
            f"📌 <b>Symbol:</b> {symbol}\n"
            f"📈 <b>Direction:</b> {direction}\n"
            "\n"
            "<b>Trade Levels</b>\n"
            f"entry @ <code>{entry_price:.2f}</code>\n"
            f"sl @ <code>{sl_price:.2f}</code>\n"
            f"tp 1 @ <code>{tp1_price:.2f}</code>\n"
            f"tp 2 @ <code>{tp2_price:.2f}</code>\n"
            "\n"
            f"🕒 <b>Timestamp:</b> {generated_at}"
        )

    def format_trade_reasoning(self, signal_obj: Any, lot_size_table: str) -> str:
        symbol = self._escape(self._get_value(signal_obj, "symbol", default="XAUUSD"))
        direction = self._escape(
            str(self._get_value(signal_obj, "signal_type", "type", default="UNKNOWN")).upper()
        )
        score = self._escape(str(self._get_value(signal_obj, "score", default="N/A")))
        reasoning_raw = str(
            self._get_value(signal_obj, "reasoning", default="No reasoning provided.")
        )
        reasoning_text, embedded_table = self._split_reasoning_and_table(reasoning_raw)
        reasoning = self._escape(reasoning_text.strip())
        rendered_table = embedded_table or lot_size_table
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        return (
            "🧠 <b>Trade Reasoning</b>\n"
            f"📌 <b>Symbol:</b> {symbol}\n"
            f"📈 <b>Direction:</b> {direction}\n"
            f"🎯 <b>Confluence Score:</b> {score}\n"
            "\n"
            "<b>Technical + Fundamental Summary</b>\n"
            f"{reasoning}\n"
            "\n"
            "<b>Risk and Position Sizing</b>\n"
            f"{rendered_table}\n"
            "\n"
            f"🕒 <b>Timestamp:</b> {generated_at}"
        )

    def format_lifecycle_update(self, update_type: str, reason: str) -> tuple[str, str]:
        normalized_type = update_type.upper()
        event_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if normalized_type == "ACTIVATED":
            alert_message = (
                "🚀 <b>Entry Triggered</b>\n"
                "🎬 GIF: https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif"
            )
            explanation_title = "Reason"
        elif normalized_type == "TP1_SMASH":
            alert_message = (
                "🎉 <b>TP 1 Smashed</b>\n"
                "🎬 GIF: https://media.giphy.com/media/111ebonMs90YLu/giphy.gif"
            )
            explanation_title = "Reason"
        elif normalized_type == "TP2_SMASH":
            alert_message = (
                "🏆 <b>TP 2 Smashed</b>\n"
                "🎬 GIF: https://media.giphy.com/media/3o7TKtnuHOHHUjR38Y/giphy.gif"
            )
            explanation_title = "Reason"
        elif normalized_type == "SL_HIT":
            alert_message = (
                "🛑 <b>SL Hit</b>\n"
                "🎬 GIF: https://media.giphy.com/media/3o6ZtaO9BZHcOjmErm/giphy.gif"
            )
            explanation_title = "Reason"
        elif normalized_type == "BE_HIT":
            alert_message = "⚖️ <b>Breakeven Exit</b>\nRunner closed at entry after TP1 was banked."
            explanation_title = "Reason"
        elif normalized_type == "EXPIRED":
            alert_message = "⌛ <b>Signal Expired</b>\nEntry was never triggered; order cancelled."
            explanation_title = "Reason"
        elif normalized_type == "TIME_STOP":
            alert_message = "⏱️ <b>Time Stop</b>\nTrade stalled without reaching TP1; closed flat."
            explanation_title = "Reason"
        else:
            raise ValueError(f"Unsupported lifecycle update type: {update_type}")

        explanation_message = (
            f"<b>{explanation_title}:</b> {self._code_wrap_prices(reason)}\n"
            f"🕒 <b>Timestamp:</b> {event_ts}"
        )
        return alert_message, explanation_message

    @staticmethod
    def _get_numeric(signal_obj: Any, *keys: str) -> float:
        value = SignalFormatter._get_value(signal_obj, *keys)
        return round(float(value), 2)

    @staticmethod
    def _escape(value: Any) -> str:
        return html.escape(str(value), quote=False)

    @staticmethod
    def _code_wrap_prices(value: Any) -> str:
        escaped = html.escape(str(value), quote=False)
        return re.sub(
            r"(?<![\w>])(\d+(?:\.\d{1,2})?)(?![\w<])",
            r"<code>\1</code>",
            escaped,
        )

    @classmethod
    def _split_reasoning_and_table(cls, reasoning: str) -> tuple[str, str]:
        marker = cls.LOT_SIZE_TABLE_MARKER
        if marker not in reasoning:
            return reasoning, ""

        text, table = reasoning.split(marker, 1)
        return text.strip(), table.strip()

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
