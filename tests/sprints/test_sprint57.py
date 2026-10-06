"""Sprint 57 (roadmap milestone 2): visible logs, market hours, feed lateness,
the owner's daily check, one row per run, and pinned-library updates."""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.alerting.owner_reports import build_daily_check
from src.analysis.market_hours import market_open, minutes_to_weekly_close
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _repo(tmp_path: Path) -> Repository:
    connection = sqlite3.connect(str(tmp_path / "m2.db"))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


def test_warnings_become_github_annotations(monkeypatch) -> None:
    from src.bot_runner import GitHubAnnotationHandler

    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    handler = GitHubAnnotationHandler(level=logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("sprint57")
    logger.addHandler(handler)
    try:
        logger.warning("spot feed down\nusing backup")
        logger.error("Telegram failed")
        logger.info("quiet")
    finally:
        logger.removeHandler(handler)
    output = buffer.getvalue()
    assert "::warning::spot feed down%0Ausing backup" in output
    assert "::error::Telegram failed" in output
    assert "quiet" not in output


def test_market_hours() -> None:
    # 2026-10-05 is a Monday.
    assert market_open("XAUUSD", _ts(2026, 10, 5, 10)) is True
    assert market_open("XAUUSD", _ts(2026, 10, 5, 21, 30)) is False  # daily break
    assert market_open("XAUUSD", _ts(2026, 10, 3, 12)) is False  # Saturday
    assert market_open("XAUUSD", _ts(2026, 10, 4, 21)) is False  # Sunday before open
    assert market_open("XAUUSD", _ts(2026, 10, 4, 23)) is True
    assert market_open("XAUUSD", _ts(2026, 10, 9, 21)) is False  # Friday after close
    assert market_open("BTCUSD", _ts(2026, 10, 3, 12)) is True
    assert minutes_to_weekly_close("XAUUSD", _ts(2026, 10, 9, 20, 25)) == 30
    assert minutes_to_weekly_close("XAUUSD", _ts(2026, 10, 8, 20, 25)) is None
    assert minutes_to_weekly_close("BTCUSD", _ts(2026, 10, 9, 20, 25)) is None


def test_late_feed_warns_owner_once(tmp_path: Path, monkeypatch) -> None:
    from src.core.orchestrator import PulseOrchestrator

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "owner")
    repository = _repo(tmp_path)
    telegram = MagicMock()
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    late = int(time.time()) - 3 * 3600
    with patch("src.analysis.market_hours.market_open", return_value=True):
        orchestrator._record_feed_health(repository, "XAUUSD", "M5", "SPOT", late)
        orchestrator._record_feed_health(repository, "XAUUSD", "M5", "SPOT", late)
    assert telegram.send_message.call_count == 1
    assert "late" in telegram.send_message.call_args.args[0]
    rows = repository.query("SELECT symbol, source, ok FROM feed_health;")
    assert rows == [("XAUUSD", "SPOT", 1), ("XAUUSD", "SPOT", 1)]
    assert orchestrator._run_feed["XAUUSD"]["source"] == "SPOT"
    repository.close()


def test_each_run_is_recorded(tmp_path: Path) -> None:
    from src.core.orchestrator import PulseOrchestrator

    db_path = tmp_path / "runs.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    now = int(time.time())
    candle = Candle("XAUUSD", "M1", now - 120, 2400.0, 2401.0, 2399.0, 2400.5, 0.0)

    class StubClient:
        last_source = {"XAUUSD": "SPOT"}

        def fetch_latest_candles(self, symbol: str, timeframe: str):
            return [candle]

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repository,
        client_factory=lambda _repo: StubClient(),
        memory_profiler=MagicMock(),
        structured_logger=MagicMock(),
    )
    orchestrator._run_macro_regime_check = MagicMock()
    orchestrator._adopt_spot_feed = MagicMock(return_value=[candle])
    orchestrator.run()

    check = sqlite3.connect(str(db_path))
    runs = check.execute("SELECT signals, errors, detail FROM pulse_runs;").fetchall()
    check.close()
    assert len(runs) == 1
    assert json.loads(runs[0][2])["XAUUSD"]["source"] == "SPOT"


def test_daily_check_reads_runs_and_feeds(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    now = int(time.time())
    for offset in range(3):
        repository.record_pulse_run(now - offset * 300, 4200, 0, 1 if offset == 0 else 0, "{}")
    repository.record_feed_health("XAUUSD", "SPOT", 120, True, now)
    text = build_daily_check(repository, now)
    assert "Runs in the last 24h: <b>3</b>" in text
    assert "with errors: <b>1</b>" in text
    assert "Gold: spot feed (TwelveData), 2 min behind" in text
    repository.close()


def test_pins_move_to_installed_versions() -> None:
    from scripts.update_pins import rewrite

    lines = ["# comment\n", "pytest==0.0.1\n", "not-installed-xyz==1.0\n"]
    output = rewrite(lines)
    assert output[0] == "# comment"
    assert output[1].startswith("pytest==") and output[1] != "pytest==0.0.1"
    assert output[2] == "not-installed-xyz==1.0"
