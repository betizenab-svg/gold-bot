from __future__ import annotations

import json
from typing import Any, Optional
from urllib import request

from config.settings import TELEGRAM_BOT_TOKEN


class TelegramClient:
    """Minimal Telegram Bot API client with reply-thread support."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        base_url: str = "https://api.telegram.org",
        timeout_seconds: int = 15,
    ) -> None:
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured")

        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": str(text),
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = str(reply_to_message_id)

        endpoint = f"{self.base_url}/bot{self.bot_token}/sendMessage"
        http_request = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)
