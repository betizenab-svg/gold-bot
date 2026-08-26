from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from config.instruments import get_instrument, state_key
from config.settings import ACTIVE_MAX_HOLD_HOURS, BE_ARM_R as BE_ARM_R_SETTING, SIGNAL_EXPIRY_MINUTES
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
        "EARLY_BE": "CLOSED_BE",
        "EXPIRED": "CANCELLED",
        "TIME_STOP": "CLOSED_TIME",
        "STRUCTURE_EXIT": "CLOSED_STRUCT",
    }

    # Realized R for terminal events (half off at TP1=1.5R, half at TP2=3R).
    EVENT_R_MAP = {
        "SL_HIT": -1.0,
        "BE_HIT": 0.75,
        "EARLY_BE": 0.0,
        "TP2_SMASH": 2.25,
        "TIME_STOP": 0.0,
    }

    # Once a trade has run this far in R, the stop moves to entry
    # (Trendline/Brooks: breakeven after the move equals the risk).
    BE_ARM_R = float(BE_ARM_R_SETTING)

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
                if triggered and candle_low <= sl_price:
                    # Filled and stopped within the same candle: pessimistic fill.
                    return "SL_HIT"
            else:
                triggered = (
                    candle_low <= entry_price
                    if order_type == "STOP"
                    else candle_high >= entry_price
                )
                if triggered and candle_high >= sl_price:
                    return "SL_HIT"
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
            if status == "ACTIVE":
                be_armed = self._breakeven_armed(signal)
                if be_armed and candle_low <= entry_price:
                    return "EARLY_BE"
                if not be_armed and candle_low <= sl_price:
                    return "SL_HIT"
            if candle_high >= tp2_price:
                return "TP2_SMASH"
            if status == "ACTIVE" and candle_high >= tp1_price:
                return "TP1_SMASH"
            return None

        if status == "PARTIAL_TP1" and candle_high >= entry_price:
            return "BE_HIT"
        if status == "ACTIVE":
            be_armed = self._breakeven_armed(signal)
            if be_armed and candle_high >= entry_price:
                return "EARLY_BE"
            if not be_armed and candle_high >= sl_price:
                return "SL_HIT"
        if candle_low <= tp2_price:
            return "TP2_SMASH"
        if status == "ACTIVE" and candle_low <= tp1_price:
            return "TP1_SMASH"
        return None

    def _breakeven_armed(self, signal: Any) -> bool:
        """True once the trade has already run >= 1R in favor (tracked MFE):
        the stop is then treated as sitting at entry."""
        try:
            mfe = float(self._get_value(signal, "mfe_r", default=0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        return mfe >= self.BE_ARM_R

    @staticmethod
    def _signal_instrument(signal: Any):
        raw = SignalLifecycleManager._get_optional_value(signal, "symbol")
        symbol = raw if isinstance(raw, str) and raw else "XAUUSD"
        return get_instrument(symbol)

    @staticmethod
    def _trading_age_seconds(
        created_ts: int, now_ts: int, weekend_closed: bool = True
    ) -> int:
        """Elapsed time minus weekend hours, so Friday signals are not mass
        expired at the Sunday reopen. 24/7 markets (crypto) count real time."""
        total = int(now_ts) - int(created_ts)
        if total <= 0:
            return 0
        if not weekend_closed:
            return total
        closed = 0
        day_cursor = int(created_ts) - (int(created_ts) % 86400)
        while day_cursor < now_ts:
            day_end = day_cursor + 86400
            weekday = datetime.fromtimestamp(day_cursor, tz=timezone.utc).weekday()
            if weekday in (5, 6):  # Saturday, Sunday
                overlap = min(day_end, int(now_ts)) - max(day_cursor, int(created_ts))
                if overlap > 0:
                    closed += overlap
            day_cursor = day_end
        return max(0, total - closed)

    def _is_pending_expired(self, signal: Any, current_candle: Candle) -> bool:
        raw_created = self._get_value(signal, "timestamp", "created_at")
        try:
            created_ts = int(raw_created)
        except (TypeError, ValueError):
            return False
        if created_ts <= 0:
            return False
        age_seconds = self._trading_age_seconds(
            created_ts,
            int(current_candle.timestamp),
            weekend_closed=not self._signal_instrument(signal).weekend_trading,
        )
        return age_seconds > int(SIGNAL_EXPIRY_MINUTES) * 60

    def _is_active_stale(self, signal: Any, current_candle: Candle) -> bool:
        raw_created = self._get_value(signal, "timestamp", "created_at")
        try:
            created_ts = int(raw_created)
        except (TypeError, ValueError):
            return False
        if created_ts <= 0:
            return False
        age_seconds = self._trading_age_seconds(
            created_ts,
            int(current_candle.timestamp),
            weekend_closed=not self._signal_instrument(signal).weekend_trading,
        )
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
            event_type = self.evaluate_signal(signal, current_candle)
            if event_type is None:
                event_type = self._structure_exit_event(active_repository, signal)
            if event_type is None:
                # Track excursions only on non-exit candles so the exit bar's
                # overshoot cannot pollute MFE/MAE calibration data.
                self._track_excursions(active_repository, signal, current_candle)
                continue

            signal_hash = str(self._get_required_value(signal, "signal_hash"))
            new_status = self.EVENT_STATUS_MAP[event_type]
            reason = self._build_lifecycle_reason(signal, event_type)
            is_closure = new_status.startswith("CLOSED") or new_status == "CANCELLED"
            if is_closure and hasattr(active_repository, "update_signal_closure"):
                # Persist WHY it closed, not just that it closed (journal/CSV).
                try:
                    active_repository.update_signal_closure(signal_hash, reason, new_status)
                except Exception as exc:
                    logging.debug("Closure reason persist failed: %s", exc)
                    active_repository.update_signal_status(signal_hash, new_status)
            else:
                active_repository.update_signal_status(signal_hash, new_status)
            self._record_risk_outcome(active_repository, event_type, current_candle, signal)

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
    def _structure_exit_event(repository: Any, signal: Any) -> Optional[str]:
        """After TP1 the runner is protected at breakeven, but a confirmed
        structure flip against it means the move is over: bank and leave
        (Brooks/Boroden: trail by structure, exit on structure break)."""
        try:
            status = str(
                SignalLifecycleManager._get_value(signal, "status", default="")
            ).upper()
            if status != "PARTIAL_TP1":
                return None
            if repository is None or not hasattr(repository, "get_kv"):
                return None
            raw_symbol = SignalLifecycleManager._get_optional_value(signal, "symbol")
            sig_symbol = raw_symbol if isinstance(raw_symbol, str) and raw_symbol else "XAUUSD"
            structure = repository.get_kv(state_key("current_structure_state", sig_symbol))
            if not isinstance(structure, str):
                return None
            direction = str(
                SignalLifecycleManager._get_value(signal, "signal_type", "type", default="")
            ).upper()
            structure = structure.upper()
            if (direction == "LONG" and structure == "BEARISH") or (
                direction == "SHORT" and structure == "BULLISH"
            ):
                return "STRUCTURE_EXIT"
        except Exception as exc:
            logging.debug("Structure exit check skipped: %s", exc)
        return None

    @staticmethod
    def _record_risk_outcome(
        repository: Any,
        event_type: str,
        current_candle: Candle,
        signal: Any = None,
    ) -> None:
        try:
            governor = RiskGovernor()
            event_ts = int(current_candle.timestamp)
            if event_type == "SL_HIT":
                governor.record_stop_loss(repository, event_ts)
            elif event_type in {"TP1_SMASH", "TP2_SMASH"}:
                governor.record_win(repository)

            r_delta = SignalLifecycleManager.EVENT_R_MAP.get(event_type)
            if event_type == "STRUCTURE_EXIT" and signal is not None:
                # Banked TP1 half (0.75R) plus the runner half marked at close.
                try:
                    entry = float(
                        SignalLifecycleManager._get_required_value(signal, "entry_price", "entry")
                    )
                    sl = float(SignalLifecycleManager._get_required_value(signal, "sl_price", "sl"))
                    direction = str(
                        SignalLifecycleManager._get_value(signal, "signal_type", "type", default="")
                    ).upper()
                    risk = abs(entry - sl)
                    if risk > 0:
                        move = float(current_candle.close) - entry
                        if direction == "SHORT":
                            move = -move
                        r_delta = 0.75 + 0.5 * (move / risk)
                except (TypeError, ValueError, AttributeError):
                    r_delta = 0.75
            if r_delta is not None:
                governor.record_result_r(repository, r_delta, event_ts)
        except Exception as exc:
            logging.debug("Risk outcome recording skipped: %s", exc)

    @staticmethod
    def _build_lifecycle_reason(signal: Any, event_type: str) -> str:
        normalized = event_type.upper()
        nd = SignalLifecycleManager._signal_instrument(signal).price_decimals
        if normalized == "ACTIVATED":
            price = float(SignalLifecycleManager._get_required_value(signal, "entry_price", "entry"))
            return f"Entry activated at {price:.{nd}f}."
        if normalized == "TP1_SMASH":
            price = float(SignalLifecycleManager._get_required_value(signal, "tp1_price", "tp1"))
            return f"Price hit TP1 at {price:.{nd}f}."
        if normalized == "TP2_SMASH":
            price = float(SignalLifecycleManager._get_required_value(signal, "tp2_price", "tp2"))
            return f"Price hit TP2 at {price:.{nd}f}."
        if normalized == "SL_HIT":
            price = float(SignalLifecycleManager._get_required_value(signal, "sl_price", "sl"))
            return f"Price hit SL at {price:.{nd}f}."
        if normalized == "BE_HIT":
            price = float(SignalLifecycleManager._get_required_value(signal, "entry_price", "entry"))
            return f"Price returned to entry at {price:.{nd}f} after TP1; runner closed at breakeven."
        if normalized == "EARLY_BE":
            price = float(SignalLifecycleManager._get_required_value(signal, "entry_price", "entry"))
            return (
                f"Trade ran +1R then returned to entry at {price:.{nd}f}; "
                "protected at breakeven instead of taking the full stop."
            )
        if normalized == "EXPIRED":
            price = float(SignalLifecycleManager._get_required_value(signal, "entry_price", "entry"))
            return f"Pending entry at {price:.{nd}f} was never triggered; signal cancelled."
        if normalized == "TIME_STOP":
            return "Trade never reached TP1 within the holding window; closed as stagnant."
        if normalized == "STRUCTURE_EXIT":
            return "Market structure flipped against the runner; closed to protect banked TP1 gains."
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

