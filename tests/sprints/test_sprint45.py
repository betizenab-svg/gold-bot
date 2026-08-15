"""Sprint 45 — chart-attached Telegram signals."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.alerting.chart_renderer import ChartRenderer
from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.alerting.telegram_client import TelegramClient
from src.domain.candle import Candle
from src.domain.signal import Signal

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _candles(count: int = 60) -> list[Candle]:
    output: list[Candle] = []
    price = 2000.0
    for index in range(count):
        drift = 0.4 if index % 3 else -0.3
        price += drift
        output.append(
            Candle(
                symbol="XAUUSD",
                timeframe="M5",
                timestamp=1_700_000_000 + index * 300,
                open=price - 0.3,
                high=price + 0.8,
                low=price - 0.9,
                close=price,
                volume=100.0,
            )
        )
    return output


def _signal() -> Signal:
    return Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2012.0,
        sl_price=2006.0,
        tp1_price=2021.0,
        tp2_price=2030.0,
        score=85,
        reasoning="chart test",
        timestamp=1_700_017_700,
        signal_hash="chart-1",
        order_type="STOP",
        strategy="PIN_BAR_REJECTION",
    )


def test_renderer_produces_png_with_zone() -> None:
    chart = ChartRenderer().render_signal_chart(
        candles=_candles(),
        signal=_signal(),
        zone={"price_top": 2010.0, "price_bottom": 2007.5},
    )
    assert chart is not None
    assert chart.startswith(PNG_MAGIC)
    assert len(chart) > 10_000  # a real rendered image, not a stub


def test_renderer_returns_none_on_insufficient_candles() -> None:
    assert ChartRenderer().render_signal_chart([], _signal()) is None
    assert ChartRenderer().render_signal_chart(_candles(1), _signal()) is None


def test_send_photo_posts_multipart_with_reply() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": {"message_id": 7788}}

    with patch("src.alerting.telegram_client.requests.post", return_value=response) as post_mock:
        client = TelegramClient(
            bot_token="token-123",
            chat_id="chat-123",
            base_url="https://api.telegram.org",
        )
        message_id = client.send_photo(b"fakepng", reply_to_message_id=4455)

    assert message_id == 7788
    args, kwargs = post_mock.call_args
    assert args[0] == "https://api.telegram.org/bottoken-123/sendPhoto"
    assert kwargs["data"]["reply_to_message_id"] == 4455
    assert kwargs["files"]["photo"][2] == "image/png"


def test_deploy_signal_sends_chart_as_reply() -> None:
    telegram_client = MagicMock()
    telegram_client.chat_id = "chat-123"
    telegram_client.send_message.side_effect = [100, 101]
    telegram_client.send_photo.return_value = 102

    manager = SignalLifecycleManager(telegram_client=telegram_client, repository=MagicMock())
    manager.deploy_signal(_signal(), sl_distance_pips=6.0, chat_id="chat-123", chart_png=b"png")

    telegram_client.send_photo.assert_called_once()
    assert telegram_client.send_photo.call_args.kwargs["reply_to_message_id"] == 100
    assert telegram_client.send_message.call_count == 2


def test_deploy_signal_survives_chart_failure() -> None:
    telegram_client = MagicMock()
    telegram_client.chat_id = "chat-123"
    telegram_client.send_message.side_effect = [100, 101]
    telegram_client.send_photo.side_effect = RuntimeError("photo boom")

    manager = SignalLifecycleManager(telegram_client=telegram_client, repository=MagicMock())
    initial_id, reasoning_id = manager.deploy_signal(
        _signal(), sl_distance_pips=6.0, chat_id="chat-123", chart_png=b"png"
    )

    assert (initial_id, reasoning_id) == (100, 101)


def test_deploy_signal_without_chart_skips_photo() -> None:
    telegram_client = MagicMock()
    telegram_client.chat_id = "chat-123"
    telegram_client.send_message.side_effect = [100, 101]

    manager = SignalLifecycleManager(telegram_client=telegram_client, repository=MagicMock())
    manager.deploy_signal(_signal(), sl_distance_pips=6.0, chat_id="chat-123")

    telegram_client.send_photo.assert_not_called()


def main() -> None:
    print("Sprint 45 chart alerts verified")


if __name__ == "__main__":
    main()
