from __future__ import annotations

import html
import re
import time
from typing import Any

from config import settings as app_settings
from config.instruments import get_instrument
from src.alerting.i18n import t, why_this_trade
from src.alerting.timefmt import eat_datetime, eat_time


def signal_code(signal_obj: Any) -> str:
    """Short public code, e.g. #G142 (market letter + signal number)."""
    getter = signal_obj.get if isinstance(signal_obj, dict) else lambda key, default=None: getattr(
        signal_obj, key, default
    )
    signal_id = getter("id", None)
    if signal_id in (None, ""):
        return ""
    instrument = get_instrument(getter("symbol", None))
    prefix = instrument.code_prefix or instrument.symbol[:1]
    return f"#{prefix}{int(signal_id)}"


class SignalFormatter:
    """Format outbound Telegram messages for initial trade alerts and lifecycle replies."""

    LOT_SIZE_TABLE_MARKER = "[LOT_SIZE_TABLE]"

    def format_initial_signal(self, signal_obj: Any) -> str:
        from src.analysis.position_sizing import LotSizeCalculator

        raw_symbol = self._get_value(signal_obj, "symbol", default="XAUUSD")
        symbol_name = raw_symbol if isinstance(raw_symbol, str) else "XAUUSD"
        instrument = get_instrument(symbol_name)
        nd = instrument.price_decimals
        direction = str(self._get_value(signal_obj, "signal_type", "type", default="UNKNOWN")).upper()
        is_long = direction == "LONG"
        entry_price = self._get_numeric(signal_obj, "entry_price", "entry", decimals=nd)
        sl_price = self._get_numeric(signal_obj, "sl_price", "sl", decimals=nd)
        tp1_price = self._get_numeric(signal_obj, "tp1_price", "tp1", decimals=nd)
        tp2_price = self._get_numeric(signal_obj, "tp2_price", "tp2", decimals=nd)
        order_type = str(self._get_value(signal_obj, "order_type", default="LIMIT") or "LIMIT").upper()
        if order_type not in {"LIMIT", "STOP", "MARKET"}:
            order_type = "LIMIT"

        lines: list[str] = []
        if bool(self._get_value(signal_obj, "trial", default=False)):
            lines.append(
                f"\U0001f9ea <b>{t('trial')}</b> (owner only): this market or strategy is still "
                "proving itself and is not shown to subscribers."
            )
        if str(self._get_value(signal_obj, "price_source", default="") or "").upper() == "BACKUP":
            lines.append(
                "\u26a0\ufe0f <b>Backup prices:</b> the main price feed was down. "
                "Check these levels against your broker's chart before entering."
            )

        code = signal_code(signal_obj)
        side = t("buy") if is_long else t("sell")
        dot = "\U0001f7e2" if is_long else "\U0001f534"
        lines.append(
            f"{dot} <b>{side} {self._escape(instrument.display_name)}</b> "
            f"({self._escape(symbol_name)})" + (f" \u00b7 <b>{code}</b>" if code else "")
        )
        lines.append(t(f"order_{order_type.lower()}_{'buy' if is_long else 'sell'}"))
        created = self._optional(signal_obj, "timestamp")
        if order_type != "MARKET" and created:
            try:
                from src.analysis.trade_windows import pending_window_seconds

                expiry = int(created) + pending_window_seconds(symbol_name)
                lines.append(t("valid_until", time=eat_time(expiry)))
            except (TypeError, ValueError):
                pass

        risk = abs(entry_price - sl_price)
        pips = risk / instrument.pip_size if instrument.pip_size else 0.0
        dollars = pips * instrument.pip_value_per_lot * 0.01
        tp1_pips = abs(tp1_price - entry_price) / instrument.pip_size if instrument.pip_size else 0.0
        tp2_pips = abs(tp2_price - entry_price) / instrument.pip_size if instrument.pip_size else 0.0
        lines += [
            "",
            f"{t('entry')} @ <code>{entry_price:.{nd}f}</code>",
            f"{t('stop')} @ <code>{sl_price:.{nd}f}</code>  (-{pips:.0f} {t('pips')} = ${dollars:.2f} {t('per_lot')})",
            f"{t('target1')} @ <code>{tp1_price:.{nd}f}</code>  (+{tp1_pips:.0f} {t('pips')}, {t('bank_half')})",
            f"{t('target2')} @ <code>{tp2_price:.{nd}f}</code>  (+{tp2_pips:.0f} {t('pips')})",
        ]
        if risk > 0:
            lines.append(
                f"{t('reward_risk')}: {abs(tp1_price - entry_price) / risk:.1f} : 1 \u2192 "
                f"{abs(tp2_price - entry_price) / risk:.1f} : 1"
            )

        strategy = self._optional(signal_obj, "strategy")
        lines += ["", f"\U0001f4a1 {t('why')}: {why_this_trade(strategy, direction)}"]

        try:
            risk_pct = float(self._optional(signal_obj, "risk_pct") or app_settings.RISK_PER_TRADE_PCT)
        except (TypeError, ValueError):
            risk_pct = float(app_settings.RISK_PER_TRADE_PCT)
        quick = LotSizeCalculator().quick_lots(entry_price, sl_price, risk_pct / 100.0, symbol_name)
        if quick:
            parts = [
                f"${row['balance']:,} \u2192 {row['lot']:.2f}"
                + (f" \u26a0 {row['risk_pct']:g}% ({t('lots_warning')})" if row["too_big"] else "")
                for row in quick
            ]
            lines.append(f"\U0001f4cf {t('lots', risk=f'{risk_pct:g}')}: " + " | ".join(parts))
        lines.append(f"\U0001f552 {t('sent')}: {eat_datetime(int(time.time()))}")
        return "\n".join(lines)

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
        generated_at = eat_datetime(int(time.time()))

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
        event_ts = eat_datetime(int(time.time()))
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
        elif normalized_type == "EARLY_BE":
            alert_message = "🛡️ <b>Breakeven Protect</b>\nTrade moved in profit, stop went to entry, closed flat (0R) instead of stopped."
            explanation_title = "Reason"
        elif normalized_type == "EXPIRED":
            alert_message = "⌛ <b>Signal Expired</b>\nEntry was never triggered; order cancelled."
            explanation_title = "Reason"
        elif normalized_type == "TIME_STOP":
            alert_message = "⏱️ <b>Time Stop</b>\nTrade stalled without reaching TP1; closed at market."
            explanation_title = "Reason"
        elif normalized_type == "STRUCTURE_EXIT":
            alert_message = "🔀 <b>Structure Exit</b>\nTrend flipped; runner closed with TP1 already banked."
            explanation_title = "Reason"
        elif normalized_type == "WEEKEND_CANCEL":
            alert_message = "📅 <b>Order Withdrawn</b>\nThe market closes for the weekend soon; this order will not open."
            explanation_title = "Reason"
        elif normalized_type in {"WEEKEND_CLOSE", "WEEKEND_RUNNER_CLOSE"}:
            alert_message = "📅 <b>Closed Before The Weekend</b>\nClose this trade now; prices can jump when the market reopens."
            explanation_title = "Reason"
        else:
            raise ValueError(f"Unsupported lifecycle update type: {update_type}")

        explanation_message = (
            f"<b>{t('reason') if explanation_title == 'Reason' else explanation_title}:</b> "
            f"{self._code_wrap_prices(reason)}\n"
            f"\U0001f552 <b>{t('time')}:</b> {event_ts}"
        )
        return alert_message, explanation_message

    @staticmethod
    def _get_numeric(signal_obj: Any, *keys: str, decimals: int = 2) -> float:
        value = SignalFormatter._get_value(signal_obj, *keys)
        return round(float(value), decimals)

    @staticmethod
    def _escape(value: Any) -> str:
        return html.escape(str(value), quote=False)

    @staticmethod
    def _code_wrap_prices(value: Any) -> str:
        escaped = html.escape(str(value), quote=False)
        return re.sub(
            r"(?<![\w>])(\d+(?:\.\d{1,5})?)(?![\w<])",
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
    def _optional(signal_obj: Any, key: str) -> Any:
        if isinstance(signal_obj, dict):
            return signal_obj.get(key)
        return getattr(signal_obj, key, None)

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
