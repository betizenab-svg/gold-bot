from __future__ import annotations

import importlib
import os
from unittest.mock import patch


def test_configuration_isolation() -> None:
    original_uat_mode = os.environ.get("UAT_MODE")
    original_uat_chat = os.environ.get("UAT_TELEGRAM_CHAT_ID")

    os.environ["UAT_MODE"] = "True"
    os.environ["UAT_TELEGRAM_CHAT_ID"] = "-1000000000001"

    try:
        settings = importlib.import_module("config.settings")
        settings = importlib.reload(settings)

        assert "uat_trading_engine.db" in settings.DB_PATH

        telegram_client_module = importlib.import_module("src.alerting.telegram_client")
        telegram_client_module = importlib.reload(telegram_client_module)
        client = telegram_client_module.TelegramClient(bot_token="dummy-token")
        assert str(client.chat_id) == "-1000000000001"
    finally:
        if original_uat_mode is None:
            os.environ.pop("UAT_MODE", None)
        else:
            os.environ["UAT_MODE"] = original_uat_mode

        if original_uat_chat is None:
            os.environ.pop("UAT_TELEGRAM_CHAT_ID", None)
        else:
            os.environ["UAT_TELEGRAM_CHAT_ID"] = original_uat_chat


class _FakePulseOrchestrator:
    def run(self, force_signal: bool = False) -> None:
        if force_signal:
            from src.alerting.telegram_client import TelegramClient

            client = TelegramClient(bot_token="dummy-token", chat_id="-1000000000001")
            client.send_message("UAT forced signal")
            client.send_message("UAT forced reasoning", reply_to_message_id=1)


def test_uat_runner_force_signal() -> None:
    import scripts.uat_runner as uat_runner

    with patch("scripts.uat_runner._get_pulse_orchestrator_class", return_value=_FakePulseOrchestrator), patch(
        "src.alerting.telegram_client.TelegramClient.send_message",
        return_value=1,
    ) as send_message_mock:
        result = uat_runner.main(["--force-signal"])

    assert result == 0
    assert send_message_mock.call_count >= 2


def main() -> int:
    test_configuration_isolation()
    test_uat_runner_force_signal()
    print("Sprint 37 UAT & Dry Run Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
