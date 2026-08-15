from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from config.settings import ACTIVE_MAX_HOLD_HOURS, SIGNAL_EXPIRY_MINUTES
from src.alerting.formatter import SignalFormatter
from src.alerting.telegram_client import TelegramClient
from src.analysis.position_sizing import LotSizeCalculator
from src.analysis.risk_governor import RiskGovernor
from src.domain.candle import Candle
from src.persistence.repository import Repository


class SignalLifecycleManager:
    """Send initial signals and monitor open signals for threaded lifecycle updates."""

    EVENT_STATUS_MAP = {
        "ACTIVATED": "ACTIVE",
        "TP1_SMASH": "PARTIAL_TP1",
        "TP2_SMASH": "CLOSED_TP2",
        "SL_HIT": "CLOSED_SL",
        "BE_HIT": "CLOSED_BE",
        "EXPIRED": "CANCELLED",
        "TIME_STOP": "CLOSED_TIME",
    }

    # Realized R for terminal events (half off at TP1=1.5R, half at TP2=3R).
    EVENT_R_MAP = {
        "SL_HIT": -1.0,
        "BE_HIT": 0.75,
        "TP2_SMASH": 2.25,
        "TIME_STOP": 0.0,
    }

    def __init__(
        self,
        telegram_client: TelegramClient,
        formatter: Optional[SignalFormatter] = None,
        lot_size_calculator: Optional[LotSizeCalculator] = None,
        repository: Optional[Repository] = None,
    ) -> None:
        self.telegram_client = telegram_client
        self.formatter = formatter or SignalFormatter()
        self.lot_size_calculator = lot_size_calculator or LotSizeCalculator()
        self.repository = repository

    def deploy_signal(
        self,
        signal_obj: Any,
        sl_distance_pips: float,
        chat_id: Optional[str] = None,
        chart_png: Optional[bytes] = None,
    ) -> tuple[int, int]:
        target_chat_id = self._get_required_value(signal_obj, "telegram_chat_id", default=chat_id)
        self._set_client_chat_id(str(target_chat_id))
        initial_message = self.formatter.format_initial_signal(signal_obj)
        message_id = self.telegram_client.send_message(initial_message)

        # Chart is best-effort decoration: never let it break the alert flow.
        if chart_png:
            try:
                self.telegram_client.send_photo(
                    chart_png,
                    reply_to_message_id=int(message_id),
                )
            except Exception as exc:
                logging.info("Signal chart delivery skipped: %s", exc)

        signal_hash = self._get_optional_value(signal_obj, "signal_hash")
        if self.repository is not None and signal_hash is not None:
            if hasattr(self.repository, "update_signal_message_id"):
                self.repository.update_signal_message_id(
                    signal_hash=str(signal_hash),
                    message_id=int(message_id),
                )
            if hasattr(self.repository, "update_signal_telegram_metadata"):
                self.repository.update_signal_telegram_metadata(
                    signal_hash=str(signal_hash),
                    telegram_message_id=str(message_id),
                    telegram_chat_id=str(target_chat_id),
                )

        entry_price = self._get_optional_value(signal_obj, "entry_price", "entry")
        sl_price = self._get_optional_value(signal_obj, "sl_price", "sl")
        if entry_price is not None and sl_price is not None:
            lot_size_table = self.lot_size_calculator.generate_table(
                float(entry_price),
                float(sl_price),
            )
        else:
            lot_size_table = self.lot_size_calculator.calculate_table(sl_distance_pips)
        reasoning_message = self.formatter.format_trade_reasoning(signal_obj, lot_size_table)
        reasoning_message_id = self.telegram_client.send_message(
            reasoning_message,
            reply_to_message_id=int(message_id),
        )
        return int(message_id), int(reasoning_message_id)

    def evaluate_signal(self, signal: Any, current_candle: Candle) -> Optional[str]:
        status = str(self._get_value(signal, "status", default="PENDING")).upper()
        direction = str(self._get_value(signal, "signal_type", "type", default="")).upper()
        entry_price = float(self._get_required_value(signal, "entry_price", "entry"))
        tp1_price = float(self._get_required_value(signal, "tp1_price", "tp1"))
        tp2_price = float(self._get_required_value(signal, "tp2_price", "tp2"))
        sl_price = float(self._get_required_value(signal, "sl_price", "sl"))
        order_type = str(self._get_value(signal, "order_type", default="LIMIT")).upper()

        candle_high = float(current_candle.high)
        candle_low = float(current_candle.low)

        if direction not in {"LONG", "SHORT"}:
            return None

        if status == "PENDING":
            if self._is_pending_expired(signal, current_candle):
                return "EXPIRED"

            if direction == "LONG":
                # STOP = breakout buy above market; LIMIT = pullback buy below market.
                triggered = (
                    candle_high >= entry_price
                    if order_type == "STOP"
                    else candle_low <= entry_price
                )
            else:
                triggered = (
                    candle_low <= entry_price
                    if order_type == "STOP"
                    else candle_high >= entry_price
                )
            return "ACTIVATED" if triggered else None

        if status not in {"ACTIVE", "PARTIAL_TP1"}:
            return None

        # Time stop: an ACTIVE trade that never paid within the window is dead
        # weight (runner after TP1 is exempt — let winners run).
        if status == "ACTIVE" and self._is_active_stale(signal, current_candle):
            return "TIME_STOP"

        if direction == "LONG":
            # Worst-case first: protective exits take priority over targets.
            if status == "PARTIAL_TP1" and candle_low <= entry_price:
                return "BE_HIT"
            if status == "ACTIVE" and candle_low <= sl_price:
                return "SL_HIT"
            if candle_high >= tp2_price:
                return "TP2_SMASH"
            if status == "ACTIVE" and candle_high >= tp1_price:
                return "TP1_SMASH"
            return None

        if status == "PARTIAL_TP1" and candle_high >= entry_price:
            return "BE_HIT"
        if status == "ACTIVE" and candle_high >= sl_price:
            return "SL_HIT"
        if candle_low <= tp2_price:
            return "TP2_SMASH"
        if status == "ACTIVE" and candle_low <= tp1_price:
            return "TP1_SMASH"
        return None

    def _is_pending_expired(self, signal: Any, current_candle: Candle) -> bool:
        raw_created = self._get_value(signal, "timestamp", "created_at")
        try:
            created_ts = int(raw_created)
        except (TypeError, ValueError):
            return False
        if created_ts <= 0:
            return False
        age_seconds = int(current_candle.timestamp) - created_ts
        return age_seconds > int(SIGNAL_EXPIRY_MINUTES) * 60

    def _is_active_stale(self, signal: Any, current_candle: Candle) -> bool:
        raw_created = self._get_value(signal, "timestamp", "created_at")
        try:
            created_ts = int(raw_created)
        except (TypeError, ValueError):
            return False
        if created_ts <= 0:
            return False
        age_seconds = int(current_candle.timestamp) - created_ts
        return age_seconds > int(ACTIVE_MAX_HOLD_HOURS) * 3600

    def process_open_signals(
        self,
        open_signals: Sequence[Any],
        current_candle: Candle,
        telegram_client: Optional[TelegramClient] = None,
        repository: Optional[Repository] = None,
        formatter: Optional[SignalFormatter] = None,
    ) -> None:
        active_repository = repository or self.repository
        active_telegram_client = telegram_client or self.telegram_client
        active_formatter = formatter or self.formatter

        if active_repository is None:
            raise ValueError("repository is required to process open signals")

        for signal in open_signals:
            self._track_excursions(active_repository, signal, current_candle)
            event_type = self.evaluate_signal(signal, current_candle)
            if event_type is None:
                continue

            signal_hash = str(self._get_required_value(signal, "signal_hash"))
            new_status = self.EVENT_STATUS_MAP[event_type]
            active_repository.update_signal_status(signal_hash, new_status)
            self._record_risk_outcome(active_repository, event_type, current_candle)

            reason = self._build_lifecycle_reason(signal, event_type)
            try:
                message_id = int(active_repository.get_signal_message_id(signal_hash))
            except KeyError:
                logging.info(
                    "No stored Telegram message id for signal %s; lifecycle reply skipped",
                    signal_hash,
                )
                continue

            chat_id = self._get_optional_value(signal, "telegram_chat_id")
            if chat_id is not None:
                self._set_client_chat_id(str(chat_id), telegram_client=active_telegram_client)

            alert_message, explanation_message = active_formatter.format_lifecycle_update(
                event_type,
                reason,
            )
            active_telegram_client.send_message(
                alert_message,
                reply_to_message_id=message_id,
            )
            active_telegram_client.send_message(
                explanation_message,
                reply_to_message_id=message_id,
            )
            logging.info(
                "Processed signal lifecycle event: signal=%s event=%s status=%s",
                signal_hash,
                event_type,
                new_status,
            )

    def send_lifecycle_update(
        self,
        signal_obj: Any,
        update_type: str,
        reason: str,
    ) -> tuple[int, int]:
        signal_hash = self._get_optional_value(signal_obj, "signal_hash")
        message_id: Optional[int] = None
        if self.repository is not None and signal_hash is not None:
            try:
                message_id = int(self.repository.get_signal_message_id(str(signal_hash)))
            except KeyError:
                logging.info(
                    "No stored Telegram message id for signal %s; falling back to signal object",
                    signal_hash,
                )

        if message_id is None:
            message_id = int(self._get_required_value(signal_obj, "telegram_message_id"))

        chat_id = self._get_optional_value(signal_obj, "telegram_chat_id")
        if chat_id is None and hasattr(self.telegram_client, "chat_id"):
            chat_id = getattr(self.telegram_client, "chat_id")
        if chat_id is None:
            raise AttributeError("signal object is missing required fields: telegram_chat_id")

        self._set_client_chat_id(str(chat_id))
        alert_message, explanation_message = self.formatter.format_lifecycle_update(
            update_type,
            reason,
        )

        alert_message_id = self.telegram_client.send_message(
            alert_message,
            reply_to_message_id=int(message_id),
        )
        explanation_message_id = self.telegram_client.send_message(
            explanation_message,
            reply_to_message_id=int(message_id),
        )

        return int(alert_message_id), int(explanation_message_id)

    @staticmethod
    def _track_excursions(repository: Any, signal: Any, current_candle: Candle) -> None:
        """Record how far each open trade ran for/against entry (in R). This is
        the raw data that calibrates stop and target placement over time."""
        try:
            status = str(
                SignalLifecycleManager._get_value(signal, "status", default="")
            ).upper()
            if status not in {"ACTIVE", "PARTIAL_TP1"}:
                return
            entry = float(
                SignalLifecycleManager._get_required_value(signal, "entry_price", "entry")
            )
            sl = float(SignalLifecycleManager._get_required_value(signal, "sl_price", "sl"))
            direction = str(
                SignalLifecycleManager._get_value(signal, "signal_type", "type", default="")
            ).upper()
            risk = abs(entry - sl)
            if risk <= 0 or direction not in {"LONG", "SHORT"}:
                return

            high = float(current_candle.high)
            low = float(current_candle.low)
            if direction == "LONG":
                mfe = max(0.0, (high - entry) / risk)
                mae = max(0.0, (entry - low) / risk)
            else:
                mfe = max(0.0, (entry - low) / risk)
                mae = max(0.0, (high - entry) / risk)

            signal_hash = SignalLifecycleManager._get_optional_value(signal, "signal_hash")
            if signal_hash is None or repository is None:
                return
            if hasattr(repository, "update_signal_excursions"):
                repository.update_signal_excursions(str(signal_hash), mfe, mae)
        except Exception as exc:
            logging.debug("Excursion tracking skipped: %s", exc)

    @staticmethod
    def _record_risk_outcome(repository: Any, event_type: str, current_candle: Candle) -> None:
        try:
            governor = RiskGovernor()
            event_ts = int(current_candle.timestamp)
            if event_type == "SL_HIT":
                governor.record_stop_loss(repository, event_ts)
            elif event_type in {"TP1_SMASH", "TP2_SMASH"}:
                governor.record_win(repository)
            r_delta = SignalLifecycleManager.EVENT_R_MAP.get(event_type)
            if r_delta is not None:
                governor.record_result_r(repository, r_delta, event_ts)
        except Exception as exc:
            logging.debug("Risk outcome recording skipped: %s", exc)

    @staticmethod
    def _build_lifecycle_reason(signal: Any, event_type: str) -> str:
        normalized = event_type.upper()
        if normalized == "ACTIVATED":
            price = float(SignalLifecycleManager._get_required_value(signal, "entry_price", "entry"))
            return f"Entry activated at {price:.2f}."
        if normalized == "TP1_SMASH":
            price = float(SignalLifecycleManager._get_required_value(signal, "tp1_price", "tp1"))
            return f"Price hit TP1 at {price:.2f}."
        if normalized == "TP2_SMASH":
            price = float(SignalLifecycleManager._get_required_value(signal, "tp2_price", "tp2"))
            return f"Price hit TP2 at {price:.2f}."
        if normalized == "SL_HIT":
            price = float(SignalLifecycleManager._get_required_value(signal, "sl_price", "sl"))
            return f"Price hit SL at {price:.2f}."
        if normalized == "BE_HIT":
            price = float(SignalLifecycleManager._get_required_value(signal, "entry_price", "entry"))
            return f"Price returned to entry at {price:.2f} after TP1; runner closed at breakeven."
        if normalized == "EXPIRED":
            price = float(SignalLifecycleManager._get_required_value(signal, "entry_price", "entry"))
            return f"Pending entry at {price:.2f} was never triggered; signal cancelled."
        if normalized == "TIME_STOP":
            return "Trade never reached TP1 within the holding window; closed as stagnant."
        raise ValueError(f"Unsupported lifecycle event type: {event_type}")

    @staticmethod
    def _get_required_value(
        signal_obj: Any,
        *keys: str,
        default: Any = None,
    ) -> Any:
        value = SignalLifecycleManager._get_optional_value(signal_obj, *keys)
        if value is not None:
            return value
        if default is not None:
            return default
        joined = ", ".join(keys)
        raise AttributeError(f"signal object is missing required fields: {joined}")

    @staticmethod
    def _get_optional_value(signal_obj: Any, *keys: str) -> Any:
        for key in keys:
            if isinstance(signal_obj, dict) and key in signal_obj:
                return signal_obj[key]
            if hasattr(signal_obj, key):
                return getattr(signal_obj, key)
        return None

    @staticmethod
    def _get_value(signal_obj: Any, *keys: str, default: Any = None) -> Any:
        value = SignalLifecycleManager._get_optional_value(signal_obj, *keys)
        if value is not None:
            return value
        return default

    def _set_client_chat_id(
        self,
        chat_id: str,
        telegram_client: Optional[TelegramClient] = None,
    ) -> None:
        target_client = telegram_client or self.telegram_client
        if hasattr(target_client, "chat_id"):
            setattr(target_client, "chat_id", str(chat_id))


class LifecycleManager(SignalLifecycleManager):
    """Backward-compatible alias for the production lifecycle manager."""

