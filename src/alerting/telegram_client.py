from __future__ import annotations

from typing import Any, Optional

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    UAT_MODE,
    UAT_TELEGRAM_CHAT_ID,
)


class TelegramAPIError(RuntimeError):
    """Raised when the Telegram Bot API request fails or returns an invalid payload."""


class TelegramClient:
    """Synchronous Telegram Bot API client for cron-safe alert delivery."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        base_url: str = "https://api.telegram.org",
        timeout_seconds: int = 15,
    ) -> None:
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        default_chat_id = UAT_TELEGRAM_CHAT_ID if UAT_MODE else TELEGRAM_CHAT_ID
        self.chat_id = chat_id or default_chat_id
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def send_message(
        self,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> int:
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is not configured")

        payload: dict[str, Any] = {
            "chat_id": str(self.chat_id),
            "text": str(text),
            "parse_mode": "HTML",
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = int(reply_to_message_id)

        endpoint = f"{self.base_url}/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(
                endpoint,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise TelegramAPIError("Telegram API request timed out") from exc
        except requests.RequestException as exc:
            raise TelegramAPIError(f"Telegram API request failed: {exc}") from exc

        if response.status_code != 200:
            raise TelegramAPIError(
                f"Telegram API returned status {response.status_code}: {response.text}"
            )

        try:
            payload_json = response.json()
            message_id = int(payload_json["result"]["message_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TelegramAPIError("Telegram API response missing result.message_id") from exc

        return message_id
