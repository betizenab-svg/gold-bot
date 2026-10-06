"""Telegram delivery with a safety net.

Every message is written to the outbox before it is sent. If Telegram is down
the message stays there and the next run delivers it, marked as delayed. A
message caught mid-send by a crash is never re-sent blindly (that could
duplicate a trade alert); the owner is told instead.
"""

from __future__ import annotations

import html
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from src.alerting.telegram_client import TelegramAPIError, TelegramClient
from src.persistence.repository import Repository

MAX_ATTEMPTS = 12
MAX_AGE_SECONDS = 24 * 3600
FLUSH_LIMIT = 20


def admin_chat_id() -> str:
    from config import settings

    return (os.getenv("TELEGRAM_ADMIN_CHAT_ID") or settings.TELEGRAM_ADMIN_CHAT_ID or "").strip()


def uses_outbox(repository: Any) -> bool:
    return isinstance(repository, Repository)


def deliver(
    telegram_client: Any,
    repository: Any,
    text: str,
    chat_id: Optional[str] = None,
    reply_to_message_id: Optional[int] = None,
    kind: str = "message",
    signal_hash: Optional[str] = None,
) -> int:
    """Send one message; on failure it is queued and the error re-raised."""
    target = str(chat_id or getattr(telegram_client, "chat_id", "") or "")
    if chat_id and hasattr(telegram_client, "chat_id"):
        setattr(telegram_client, "chat_id", str(chat_id))
    if not uses_outbox(repository) or not target:
        return int(telegram_client.send_message(text, reply_to_message_id=reply_to_message_id))

    row_id = repository.outbox_add(
        target, text, kind=kind, reply_to_message_id=reply_to_message_id,
        signal_hash=signal_hash, status="SENDING",
    )
    try:
        message_id = int(telegram_client.send_message(text, reply_to_message_id=reply_to_message_id))
    except (TelegramAPIError, ValueError) as exc:
        repository.outbox_mark(row_id, "PENDING", error=str(exc))
        raise
    repository.outbox_mark(row_id, "SENT", message_id=message_id)
    return message_id


def notify_admin(text: str, repository: Any = None, telegram_client: Any = None) -> bool:
    """Owner-only message. Without an admin chat it is only logged."""
    chat = admin_chat_id()
    if not chat:
        logging.warning("[owner] %s", html.unescape(_strip_tags(text)))
        return False
    client = telegram_client or TelegramClient(chat_id=chat)
    if hasattr(client, "chat_id"):
        setattr(client, "chat_id", chat)
    try:
        deliver(client, repository, text, chat_id=chat, kind="admin")
        return True
    except (TelegramAPIError, ValueError) as exc:
        logging.error("Owner message not delivered (queued): %s", exc)
        return False


def _strip_tags(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", str(text))


class OutboxFlusher:
    """Runs at the start of each pulse."""

    def __init__(self, repository: Repository, client_factory: Any = None) -> None:
        self.repository = repository
        self.client_factory = client_factory or TelegramClient

    def recover_interrupted(self) -> int:
        rows = self.repository.outbox_rows(["SENDING"])
        for row in rows:
            self.repository.outbox_mark(int(row["id"]), "UNCERTAIN")
        if rows:
            notify_admin(
                "\u26a0\ufe0f <b>Possible missed or double message</b>\n"
                f"{len(rows)} Telegram message(s) were being sent when the last run "
                "stopped. They were NOT re-sent (to avoid duplicates). Check the "
                "channel and post a correction if one is missing.",
                repository=self.repository,
            )
        return len(rows)

    def flush(self) -> int:
        delivered = 0
        now = int(time.time())
        for row in self.repository.outbox_rows(["PENDING"], limit=FLUSH_LIMIT):
            row_id = int(row["id"])
            if row["attempts"] >= MAX_ATTEMPTS or now - int(row["created_at"]) > MAX_AGE_SECONDS:
                self.repository.outbox_mark(row_id, "FAILED")
                continue
            reply_to = row.get("reply_to_message_id")
            if row.get("kind") == "reply" and reply_to is None and row.get("signal_hash"):
                try:
                    reply_to = self.repository.get_signal_message_id(str(row["signal_hash"]))
                except KeyError:
                    continue
            created = datetime.fromtimestamp(int(row["created_at"]), tz=timezone.utc)
            text = (
                f"\u23f3 <i>Delayed message from {created.strftime('%H:%M')} UTC "
                "(Telegram was unreachable)</i>\n" + str(row["text"] or "")
            )
            client = self.client_factory()
            if hasattr(client, "chat_id"):
                setattr(client, "chat_id", str(row["chat_id"]))
            self.repository.outbox_mark(row_id, "SENDING")
            try:
                message_id = int(client.send_message(text, reply_to_message_id=reply_to))
            except (TelegramAPIError, ValueError) as exc:
                self.repository.outbox_mark(row_id, "PENDING", error=str(exc))
                break
            self.repository.outbox_mark(row_id, "SENT", message_id=message_id)
            delivered += 1
            if row.get("kind") == "signal" and row.get("signal_hash"):
                self.repository.update_signal_message_id(str(row["signal_hash"]), message_id)
                self.repository.update_signal_telegram_metadata(
                    str(row["signal_hash"]), str(message_id), str(row["chat_id"])
                )
        return delivered
