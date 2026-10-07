"""Sprint 63 (roadmap milestone 8): VIP channel routing, free-channel copies
and results, partner relay. (The membership bot itself is tested with
`node --test` in cloudflare/.)"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.alerting.channels import copy_targets, free_channel_has_copy, result_teaser, send_copies, send_copy_updates
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer

T0 = int(datetime(2026, 10, 7, 9, tzinfo=timezone.utc).timestamp())


def _repo(tmp_path: Path) -> Repository:
    connection = sqlite3.connect(str(tmp_path / "m8.db"))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


def test_copies_only_where_set(tmp_path: Path, monkeypatch) -> None:
    repository = _repo(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "free")
    assert copy_targets(repository, T0) == []  # one public channel: no copies
    monkeypatch.setenv("PARTNER_CHAT_IDS", "p1, p2")
    assert copy_targets(repository, T0) == ["p1", "p2"]
    monkeypatch.setenv("TELEGRAM_VIP_CHAT_ID", "vip")
    monkeypatch.setenv("FREE_SIGNALS_PER_DAY", "1")
    assert copy_targets(repository, T0) == ["p1", "p2", "free"]  # first signal of the day
    assert copy_targets(repository, T0 + 3600) == ["p1", "p2"]
    assert copy_targets(repository, T0 + 86400) == ["p1", "p2", "free"]  # next day
    repository.close()


def test_copies_are_threaded_with_their_updates(tmp_path: Path, monkeypatch) -> None:
    repository = _repo(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "free")
    clients: dict[str, MagicMock] = {}

    def factory(chat: str) -> MagicMock:
        client = clients.setdefault(chat, MagicMock(chat_id=chat))
        client.send_message.return_value = 500 + len(clients)
        return client

    copies = send_copies(repository, "h1", "card", ["p1", "free"], client_factory=factory)
    assert set(copies) == {"p1", "free"} and free_channel_has_copy(repository, "h1")
    assert send_copy_updates(repository, "h1", ["TP 1 hit", "reason"], client_factory=factory) == 4
    reply = clients["p1"].send_message.call_args_list[-1]
    assert reply.kwargs["reply_to_message_id"] == copies["p1"]
    assert "Subscribe to VIP" not in result_teaser("#G1", "Gold", 2.25)
    monkeypatch.setenv("VIP_JOIN_URL", "https://t.me/Bot?start=join")
    monkeypatch.setenv("TELEGRAM_VIP_CHAT_ID", "vip")
    teaser = result_teaser("#G1", "Gold", 2.25)
    assert "+2.25R" in teaser and "Subscribe to VIP for full access" in teaser and "start=join" in teaser
    repository.close()


def test_free_signal_copy_invites_to_vip_but_partner_copies_do_not(tmp_path: Path, monkeypatch) -> None:
    repository = _repo(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "free")
    monkeypatch.setenv("TELEGRAM_VIP_CHAT_ID", "vip")
    monkeypatch.setenv("VIP_JOIN_URL", "https://t.me/Bot?start=join")
    clients: dict[str, MagicMock] = {}

    def factory(chat: str) -> MagicMock:
        client = clients.setdefault(chat, MagicMock(chat_id=chat))
        client.send_message.return_value = 700
        return client

    send_copies(repository, "h9", "card", ["p1", "free"], client_factory=factory)
    free_text = clients["free"].send_message.call_args.args[0]
    assert free_text.startswith("card") and "Subscribe to VIP for full access" in free_text
    assert clients["p1"].send_message.call_args.args[0] == "card"
    repository.close()


def test_vip_mode_routes_signals_and_tells_the_free_channel_the_result(tmp_path: Path, monkeypatch) -> None:
    from src.alerting.lifecycle_manager import SignalLifecycleManager
    from src.core.orchestrator import PulseOrchestrator

    repository = _repo(tmp_path)
    monkeypatch.setenv("TELEGRAM_VIP_CHAT_ID", "vip")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "free")
    monkeypatch.setenv("FREE_SIGNALS_PER_DAY", "0")
    sent: list[tuple[str, str]] = []
    telegram = MagicMock()
    telegram.chat_id = "free"
    telegram.send_message.side_effect = lambda text, **kw: sent.append((telegram.chat_id, text)) or len(sent)
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    candles = [Candle("XAUUSD", "M5", T0 + 300 * i, 4196.0, 4199.0, 4193.0, 4197.0, 0) for i in range(20)]
    setup = {"trade_direction": "LONG", "strategy": "PIN_BAR_REJECTION", "entry_price": 4196.4, "sl_price": 4187.86}
    orchestrator._persist_actionable_signal(repository, setup, candles, candles[-1], 82)
    assert sent and all(chat == "vip" for chat, _ in sent)

    teasers: list[str] = []

    class FakeClient:
        def __init__(self, chat_id: str) -> None:
            self.chat_id = chat_id

        def send_message(self, text: str, reply_to_message_id=None) -> int:
            teasers.append(text)
            return 99

    monkeypatch.setattr("src.alerting.lifecycle_manager.TelegramClient", FakeClient)
    signal = repository.get_open_signals()[0]
    repository.update_signal_status(signal.signal_hash, "ACTIVE")
    signal = repository.get_open_signals()[0]
    manager = SignalLifecycleManager(telegram_client=telegram, repository=repository)
    stop_hit = Candle("XAUUSD", "M5", T0 + 7200, 4185.0, 4190.0, 4180.0, 4182.0, 0)
    manager.process_open_signals([signal], stop_hit)
    assert teasers and "VIP signal" in teasers[0] and "-1.00R" in teasers[0]
    repository.close()
