"""Where signals go.

Today: one public channel gets everything. Once a VIP channel is set
(TELEGRAM_VIP_CHAT_ID), full signals go to VIP; the free channel gets the
first FREE_SIGNALS_PER_DAY signals of each day as copies, and a short result
line for every VIP trade that closes. Partner channels (PARTNER_CHAT_IDS,
off unless set) receive copies of every public signal and its updates.
Trial signals are never copied anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Iterable, Optional

from src.alerting.messenger import deliver
from src.alerting.telegram_client import TelegramAPIError, TelegramClient


def vip_chat_id() -> str:
    return (os.getenv("TELEGRAM_VIP_CHAT_ID") or "").strip()


def free_chat_id() -> str:
    from config import settings

    return (os.getenv("TELEGRAM_CHAT_ID") or settings.TELEGRAM_CHAT_ID or "").strip()


def partner_chat_ids() -> list[str]:
    return [part.strip() for part in (os.getenv("PARTNER_CHAT_IDS") or "").split(",") if part.strip()]


def free_signals_per_day() -> int:
    try:
        return max(0, int(os.getenv("FREE_SIGNALS_PER_DAY") or "1"))
    except ValueError:
        return 1


def _copies_key(signal_hash: str) -> str:
    return f"signal_copies:{signal_hash}"


def copy_targets(repository: Any, signal_timestamp: int) -> list[str]:
    """Extra chats for a new public signal (free-channel copy in VIP mode, partners)."""
    targets = list(partner_chat_ids())
    if vip_chat_id() and free_chat_id():
        day = time.strftime("%Y-%m-%d", time.gmtime(int(signal_timestamp)))
        key = f"free_signal_copies:{day}"
        try:
            used = int(repository.get_kv(key) or 0)
        except (TypeError, ValueError):
            used = 0
        if used < free_signals_per_day():
            repository.set_kv(key, str(used + 1))
            targets.append(free_chat_id())
    return [chat for chat in dict.fromkeys(targets) if chat]


def send_copies(
    repository: Any,
    signal_hash: str,
    text: str,
    targets: Iterable[str],
    client_factory: Callable[[str], Any] = lambda chat: TelegramClient(chat_id=chat),
) -> dict[str, int]:
    copies: dict[str, int] = {}
    for chat in targets:
        try:
            copies[chat] = deliver(client_factory(chat), repository, text, chat_id=chat, kind="copy",
                                   signal_hash=signal_hash)
        except (TelegramAPIError, ValueError) as exc:
            logging.error("Signal copy to %s not delivered: %s", chat, exc)
    if copies:
        repository.set_kv(_copies_key(signal_hash), json.dumps(copies))
    return copies


def send_copy_updates(
    repository: Any,
    signal_hash: str,
    texts: Iterable[str],
    client_factory: Callable[[str], Any] = lambda chat: TelegramClient(chat_id=chat),
) -> int:
    """Thread lifecycle updates under each copy of the signal."""
    try:
        copies = json.loads(repository.get_kv(_copies_key(signal_hash)) or "{}")
    except (TypeError, ValueError):
        return 0
    sent = 0
    for chat, message_id in copies.items():
        for text in texts:
            try:
                deliver(client_factory(chat), repository, text, chat_id=chat,
                        reply_to_message_id=int(message_id), kind="copy", signal_hash=signal_hash)
                sent += 1
            except (TelegramAPIError, ValueError) as exc:
                logging.error("Copy update to %s not delivered: %s", chat, exc)
    return sent


def free_channel_has_copy(repository: Any, signal_hash: str) -> bool:
    try:
        copies = json.loads(repository.get_kv(_copies_key(signal_hash)) or "{}")
    except (TypeError, ValueError):
        return False
    return free_chat_id() in copies


def result_teaser(code: str, market: str, result_r: Optional[float]) -> str:
    join = (os.getenv("VIP_JOIN_URL") or "").strip()
    outcome = f"{result_r:+.2f}R" if result_r is not None else "closed"
    return (
        f"\U0001f512 VIP signal <b>{code}</b> ({market}) closed: <b>{outcome}</b>."
        + (f"\nJoin VIP: {join}" if join else "")
    )
