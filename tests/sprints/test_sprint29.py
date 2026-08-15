import sqlite3
from unittest.mock import MagicMock, patch

from src.alerting.telegram_client import TelegramClient
from src.domain.signal import Signal
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def test_initial_message_sending_and_id_capture() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": {"message_id": 4455}}

    with patch("src.alerting.telegram_client.requests.post", return_value=response) as post_mock:
        client = TelegramClient(
            bot_token="token-123",
            chat_id="chat-123",
            base_url="https://api.telegram.org",
        )
        message_id = client.send_message("Initial Signal")

    assert message_id == 4455
    assert isinstance(message_id, int)
    args, kwargs = post_mock.call_args
    assert args[0] == "https://api.telegram.org/bottoken-123/sendMessage"
    assert kwargs["json"]["parse_mode"] == "HTML"
    assert "reply_to_message_id" not in kwargs["json"]


def test_threaded_reply_generation() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": {"message_id": 4456}}

    with patch("src.alerting.telegram_client.requests.post", return_value=response) as post_mock:
        client = TelegramClient(bot_token="token-123", chat_id="chat-123")
        message_id = client.send_message("Trade Reason", reply_to_message_id=4455)

    assert message_id == 4456
    assert post_mock.call_args.kwargs["json"]["reply_to_message_id"] == 4455


def test_database_persistence() -> None:
    connection = sqlite3.connect(":memory:")
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    signal = Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2000.0,
        sl_price=1990.0,
        tp1_price=2010.0,
        tp2_price=2020.0,
        score=80,
        reasoning="Test signal",
        timestamp=1_700_000_000,
        signal_hash="abc123",
    )
    repository.save_signal(signal)

    repository.update_signal_message_id("abc123", 4455)
    assert repository.get_signal_message_id("abc123") == 4455


def main() -> None:
    test_initial_message_sending_and_id_capture()
    test_threaded_reply_generation()
    test_database_persistence()
    print("Sprint 29 Telegram Infrastructure Verified")


if __name__ == "__main__":
    main()
