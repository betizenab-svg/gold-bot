from __future__ import annotations

from typing import Any, Optional

from src.alerting.formatter import SignalFormatter
from src.alerting.telegram_client import TelegramClient
from src.analysis.position_sizing import LotSizeCalculator
from src.persistence.repository import Repository


class LifecycleManager:
    """Send initial signals, threaded reasoning, and threaded lifecycle replies."""

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
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target_chat_id = self._get_required_value(signal_obj, "telegram_chat_id", default=chat_id)
        initial_message = self.formatter.format_initial_signal(signal_obj)
        initial_response = self.telegram_client.send_message(
            chat_id=str(target_chat_id),
            text=initial_message,
        )

        message_id = self._extract_message_id(initial_response)
        if message_id is None:
            raise ValueError("Telegram response did not include a message_id")

        signal_hash = self._get_optional_value(signal_obj, "signal_hash")
        if self.repository is not None and signal_hash is not None:
            self.repository.update_signal_telegram_metadata(
                signal_hash=str(signal_hash),
                telegram_message_id=str(message_id),
                telegram_chat_id=str(target_chat_id),
            )

        lot_size_table = self.lot_size_calculator.calculate_table(sl_distance_pips)
        reasoning_message = self.formatter.format_trade_reasoning(signal_obj, lot_size_table)
        reasoning_response = self.telegram_client.send_message(
            chat_id=str(target_chat_id),
            text=reasoning_message,
            reply_to_message_id=str(message_id),
        )
        return initial_response, reasoning_response

    def send_lifecycle_update(
        self,
        signal_obj: Any,
        update_type: str,
        reason: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        chat_id = self._get_required_value(signal_obj, "telegram_chat_id")
        message_id = self._get_required_value(signal_obj, "telegram_message_id")
        alert_message, explanation_message = self.formatter.format_lifecycle_update(
            update_type,
            reason,
        )

        alert_response = self.telegram_client.send_message(
            chat_id=str(chat_id),
            text=alert_message,
            reply_to_message_id=str(message_id),
        )
        explanation_response = self.telegram_client.send_message(
            chat_id=str(chat_id),
            text=explanation_message,
            reply_to_message_id=str(message_id),
        )

        signal_hash = self._get_optional_value(signal_obj, "signal_hash")
        if self.repository is not None and signal_hash is not None:
            self.repository.update_signal_closure(
                signal_hash=str(signal_hash),
                closure_reason=reason,
                status=update_type.upper(),
            )

        return alert_response, explanation_response

    @staticmethod
    def _extract_message_id(response_payload: dict[str, Any]) -> Optional[int]:
        if not isinstance(response_payload, dict):
            return None

        result = response_payload.get("result")
        if isinstance(result, dict) and "message_id" in result:
            return int(result["message_id"])

        if "message_id" in response_payload:
            return int(response_payload["message_id"])

        return None

    @staticmethod
    def _get_required_value(
        signal_obj: Any,
        *keys: str,
        default: Any = None,
    ) -> Any:
        value = LifecycleManager._get_optional_value(signal_obj, *keys)
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
