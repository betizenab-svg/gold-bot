"""Sprint 56 (roadmap milestone 1): honest results, spot gold, big-picture
checks as score penalties, flat risk, owner chat, outbox, shadow ideas."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.alerting.messenger import OutboxFlusher, deliver, notify_admin
from src.alerting.telegram_client import TelegramAPIError
from src.analysis.filters import PermissionEngine
from src.analysis.outcomes import derive_realized_r, realized_r_for_event, row_r
from src.analysis.scoring import ScoringEngine
from src.analysis.signal_factory import risk_fraction_for_score
from src.analysis.sovereign import SovereignProxy
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.ingestion.factory import MarketDataRouter, basis_key, feed_kind_key
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.resilience.circuit_breaker import CircuitBreaker

T0 = 1_790_000_000  # a Monday


def _repo(tmp_path: Path, name: str = "m1.db") -> Repository:
    connection = sqlite3.connect(str(tmp_path / name))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


def _signal(signal_hash: str = "s1", **overrides) -> Signal:
    values = dict(
        symbol="XAUUSD", signal_type="LONG", entry_price=2400.0, sl_price=2395.0,
        tp1_price=2407.5, tp2_price=2415.0, score=80, reasoning="test",
        timestamp=T0, signal_hash=signal_hash, order_type="LIMIT", strategy="ZONE_BOUNCE",
    )
    values.update(overrides)
    return Signal(**values)


def _candle(offset: int, high: float, low: float, close: float, symbol: str = "XAUUSD") -> Candle:
    return Candle(symbol, "M5", T0 + offset, (high + low) / 2, high, low, close, 0.0)


# --- Exact results -------------------------------------------------------------

def test_exact_r_per_event() -> None:
    assert realized_r_for_event("SL_HIT", "LONG", 100, 95, 115) == -1.0
    assert realized_r_for_event("EARLY_BE", "LONG", 100, 95, 115) == 0.0
    assert realized_r_for_event("BE_HIT", "LONG", 100, 95, 115) == 0.75
    assert realized_r_for_event("TP2_SMASH", "LONG", 100, 95, 115) == 2.25
    # TP2 capped at 2R by the measured move: half at 1.5R + half at 2R.
    assert realized_r_for_event("TP2_SMASH", "SHORT", 100, 105, 90) == 1.75
    assert realized_r_for_event("TIME_STOP", "LONG", 100, 95, 115, exit_price=102.5) == 0.5
    assert realized_r_for_event("EXPIRED", "LONG", 100, 95, 115) is None


def test_old_early_breakevens_are_recounted_as_zero(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    connection = sqlite3.connect(str(db))
    connection.execute(
        "CREATE TABLE signals (id INTEGER PRIMARY KEY, signal_hash TEXT, symbol TEXT, "
        "type TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, status TEXT, "
        "closure_reason TEXT, created_at INTEGER);"
    )
    rows = [
        ("a", "CLOSED_BE", "Trade ran +1R then returned to entry at 2400.00; protected"),
        ("b", "CLOSED_BE", "Price returned to entry at 2400.00 after TP1; runner closed"),
        ("c", "CLOSED_TP2", "Price hit TP2"),
    ]
    for signal_hash, status, reason in rows:
        connection.execute(
            "INSERT INTO signals (signal_hash, symbol, type, entry, sl, tp1, tp2, status, "
            "closure_reason, created_at) VALUES (?, 'XAUUSD', 'LONG', 2400, 2395, 2407.5, "
            "2410, ?, ?, 0);",
            (signal_hash, status, reason),
        )
    connection.commit()
    SchemaInitializer(connection).initialize()
    values = dict(connection.execute("SELECT signal_hash, realized_r FROM signals;").fetchall())
    assert values == {"a": 0.0, "b": 0.75, "c": 1.75}
    assert derive_realized_r("CLOSED_SL", "", "LONG", 1, 0, 2) == -1.0
    assert row_r("CLOSED_BE", None) == 0.75
    assert row_r("CLOSED_BE", 0.0) == 0.0


def test_lifecycle_stores_exact_result(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    repository.save_signal(_signal(mfe_r=0.0))
    repository.update_signal_status("s1", "ACTIVE")
    repository.update_signal_message_id("s1", 50)
    telegram = MagicMock()
    telegram.send_message.side_effect = [1, 2, 3, 4]
    manager = SignalLifecycleManager(telegram_client=telegram, repository=repository)

    # Runs +1R (arms breakeven at 0.75R), then comes back to entry: 0R, not +0.75R.
    manager.process_open_signals(
        repository.get_open_signals(), _candle(300, 2405.0, 2401.0, 2404.0), telegram, repository
    )
    manager.process_open_signals(
        repository.get_open_signals(), _candle(600, 2402.0, 2399.0, 2399.5), telegram, repository
    )
    status, realized, closed_at = repository.query(
        "SELECT status, realized_r, closed_at FROM signals WHERE signal_hash = 's1';"
    )[0]
    assert status == "CLOSED_BE"
    assert realized == 0.0
    assert closed_at == T0 + 600
    repository.close()


# --- Price feeds ---------------------------------------------------------------

class _FakeSource:
    def __init__(self, candles=None, error=None) -> None:
        self.candles = candles or []
        self.error = error
        self.calls = 0

    def fetch_latest_candles(self, symbol: str, timeframe: str):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.candles)


def test_router_prefers_spot_and_backs_up_with_basis(tmp_path: Path) -> None:
    from src.ingestion.twelvedata import DataIngestionError as SpotError

    repository = _repo(tmp_path)
    spot = _FakeSource([_candle(0, 2401.0, 2399.0, 2400.0)])
    yahoo = _FakeSource([_candle(0, 2426.0, 2424.0, 2425.0)])
    router = MarketDataRouter(
        repository, yahoo_client=yahoo, spot_client=spot, futures_closes=lambda s, tf: {}
    )
    assert router.fetch_latest_candles("XAUUSD", "M5")[0].close == 2400.0
    assert router.last_source["XAUUSD"] == "SPOT"
    assert yahoo.calls == 0

    # Spot down, market already on spot, gap of +25 measured recently.
    repository.set_kv(feed_kind_key("XAUUSD"), "SPOT")
    repository.set_kv(basis_key("XAUUSD"), json.dumps({"value": 25.0, "measured_at": int(time.time())}))
    spot.error = SpotError("down")
    backup = router.fetch_latest_candles("XAUUSD", "M5")
    assert router.last_source["XAUUSD"] == "BACKUP"
    assert backup[0].close == 2400.0

    # No fresh gap: skip rather than feed mismatched prices.
    repository.set_kv(basis_key("XAUUSD"), json.dumps({"value": 25.0, "measured_at": 0}))
    assert router.fetch_latest_candles("XAUUSD", "M5") == []
    assert router.last_source["XAUUSD"] == "NONE"

    # Markets without a spot feed stay on Yahoo.
    assert router.fetch_latest_candles("EURUSD", "M5")
    assert router.last_source["EURUSD"] == "YAHOO"
    repository.close()


def test_router_measures_futures_spot_gap(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    stored = [_candle(i * 300, 2401.0, 2399.0, 2400.0 + i) for i in range(5)]
    repository.save_candles(stored)
    futures = {c.timestamp: c.close + 24.5 for c in stored}
    router = MarketDataRouter(
        repository,
        yahoo_client=_FakeSource(),
        spot_client=_FakeSource([]),
        futures_closes=lambda s, tf: futures,
    )
    router.fetch_latest_candles("XAUUSD", "M5")
    assert router.fresh_basis("XAUUSD") == 24.5
    repository.close()


def test_twelvedata_skips_forming_bar_and_reads_errors(tmp_path: Path, monkeypatch) -> None:
    import src.ingestion.twelvedata as td

    monkeypatch.setattr(td, "TWELVEDATA_API_KEY", "k")
    repository = _repo(tmp_path)
    now = int(time.time())
    closed_bar = now - (now % 300) - 600
    forming_bar = now - (now % 300)
    repository.set_kv("last_processed_XAUUSD", str(closed_bar - 300))

    def fmt(ts: int) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "status": "ok",
        "values": [
            {"datetime": fmt(forming_bar), "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
            {"datetime": fmt(closed_bar), "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
        ],
    }
    with patch("src.ingestion.twelvedata.requests.get", return_value=response) as get:
        candles = td.TwelveDataClient(repository).fetch_latest_candles("XAUUSD", "M5")
    assert [c.timestamp for c in candles] == [closed_bar]
    assert get.call_args.kwargs["params"]["timezone"] == "UTC"
    assert get.call_args.kwargs["params"]["symbol"] == "XAU/USD"

    response.json.return_value = {"status": "error", "code": 429, "message": "out of credits"}
    with patch("src.ingestion.twelvedata.requests.get", return_value=response):
        with pytest.raises(td.DataIngestionError):
            td.TwelveDataClient(repository).fetch_latest_candles("XAUUSD", "M5")
    repository.close()


def test_breakers_are_per_provider(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    breaker = CircuitBreaker(repository)
    for _ in range(3):
        breaker.record_failure("TWELVEDATA", "429", "credits")
    assert breaker.is_open("TWELVEDATA") is True
    assert breaker.is_open("YAHOO") is False
    assert repository.get_kv("active_provider") is None
    repository.close()


def test_switch_to_spot_clears_futures_history(tmp_path: Path) -> None:
    from src.core.orchestrator import PulseOrchestrator

    repository = _repo(tmp_path)
    repository.save_candles([_candle(0, 2426.0, 2424.0, 2425.0)])
    repository.save_zone({"symbol": "XAUUSD", "timeframe": "M5", "type": "OB_BULLISH",
                          "price_top": 2420.0, "price_bottom": 2410.0, "status": "ACTIVE"})
    repository.set_kv("swing_history", "{}")
    repository.save_signal(_signal("old"))
    repository.update_signal_message_id("old", 77)
    repository.update_signal_telegram_metadata("old", "77", "public")

    spot_bars = [_candle(i * 300, 2401.0, 2399.0, 2400.0) for i in range(3)]
    client = MagicMock()
    client.fetch_latest_candles.return_value = spot_bars
    telegram = MagicMock()
    telegram.send_message.return_value = 9
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    loaded = orchestrator._adopt_spot_feed(repository, client, "XAUUSD", "M5")

    assert loaded == spot_bars
    assert repository.get_kv(feed_kind_key("XAUUSD")) == "SPOT"
    assert repository.get_kv("swing_history") is None
    assert repository.query("SELECT COUNT(*) FROM market_data;")[0][0] == 0
    assert repository.query("SELECT COUNT(*) FROM zones;")[0][0] == 0
    assert repository.query("SELECT status FROM signals WHERE signal_hash='old';")[0][0] == "CANCELLED"
    assert "withdrawn" in telegram.send_message.call_args_list[0].args[0].lower()
    repository.close()


# --- Big-picture checks, central banks, sizing, scoring -------------------------

def test_macro_checks_lower_the_score_by_default() -> None:
    engine = PermissionEngine()
    setup = {"trade_direction": "SHORT"}
    macro = {"macro_long_bias_multiplier": "1.25", "macro_cot_state": "NEUTRAL"}
    assert engine.is_trade_permitted(setup, macro, mode="penalty") == (True, "Permitted")
    penalties = engine.score_penalties(setup, macro, mode="penalty")
    assert len(penalties) == 1 and penalties[0][0] == 10
    assert engine.score_penalties(setup, macro, mode="off") == []


def test_central_bank_figure_must_be_real_and_recent(monkeypatch) -> None:
    from datetime import datetime, timezone

    from config import settings

    proxy = SovereignProxy()
    monkeypatch.setattr(settings, "CB_NET_PURCHASES_TONNES", "")
    monkeypatch.setattr(settings, "CB_NET_PURCHASES_QUARTER", "")
    assert proxy.manual_figure() is None

    monkeypatch.setattr(settings, "CB_NET_PURCHASES_TONNES", "410")
    monkeypatch.setattr(settings, "CB_NET_PURCHASES_QUARTER", "2026-Q2")
    october = datetime(2026, 10, 6, tzinfo=timezone.utc)
    assert proxy.manual_figure(now=october) == (410.0, "2026-Q2")
    assert proxy.manual_figure(now=datetime(2027, 6, 1, tzinfo=timezone.utc)) is None


def test_flat_risk_unless_conviction_sizing(monkeypatch) -> None:
    import src.analysis.signal_factory as factory

    assert risk_fraction_for_score(95) == pytest.approx(0.01)
    monkeypatch.setattr(factory, "CONVICTION_SIZING_ENABLED", True)
    assert factory.risk_fraction_for_score(95) == pytest.approx(0.02)
    assert factory.risk_fraction_for_score(80) == pytest.approx(0.01)


def test_neutral_bias_scores_the_same_for_every_spelling() -> None:
    engine = ScoringEngine()
    assert engine.score_macro_bias("LONG", "BIAS_NEUTRAL") == 10
    assert engine.score_macro_bias("LONG", "NEUTRAL") == 10


# --- Owner chat and outbox ---------------------------------------------------------

def test_owner_messages_never_reach_the_public_channel(monkeypatch, caplog) -> None:
    telegram = MagicMock()
    telegram.chat_id = "public"
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "")
    with caplog.at_level("WARNING"):
        assert notify_admin("<b>feed down</b>", telegram_client=telegram) is False
    telegram.send_message.assert_not_called()
    assert "feed down" in caplog.text

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "owner")
    assert notify_admin("feed down", telegram_client=telegram) is True
    assert telegram.chat_id == "owner"


def test_outbox_retries_and_never_blindly_resends(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "")
    repository = _repo(tmp_path)
    telegram = MagicMock()
    telegram.chat_id = "public"
    telegram.send_message.side_effect = TelegramAPIError("down")
    with pytest.raises(TelegramAPIError):
        deliver(telegram, repository, "TP1 hit", reply_to_message_id=5, kind="reply")
    assert repository.outbox_rows(["PENDING"])[0]["text"] == "TP1 hit"

    telegram.send_message.side_effect = None
    telegram.send_message.return_value = 99
    flusher = OutboxFlusher(repository, client_factory=lambda: telegram)
    assert flusher.flush() == 1
    sent_text = telegram.send_message.call_args.args[0]
    assert sent_text.startswith("\u23f3") and "TP1 hit" in sent_text
    assert telegram.send_message.call_args.kwargs["reply_to_message_id"] == 5

    # A message caught mid-send by a crash is reported, not re-sent.
    repository.outbox_add("public", "maybe sent", status="SENDING")
    telegram.send_message.reset_mock()
    assert flusher.recover_interrupted() == 1
    assert flusher.flush() == 0
    telegram.send_message.assert_not_called()
    assert repository.outbox_rows(["UNCERTAIN"])[0]["text"] == "maybe sent"
    repository.close()


def test_queued_signal_card_threads_later_updates(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    repository.save_signal(_signal(telegram_chat_id="public"))
    telegram = MagicMock()
    telegram.chat_id = "public"
    telegram.send_message.side_effect = TelegramAPIError("down")
    manager = SignalLifecycleManager(telegram_client=telegram, repository=repository)
    with pytest.raises(TelegramAPIError):
        manager.deploy_signal(_signal(telegram_chat_id="public"), sl_distance_pips=50.0, chat_id="public")
    kinds = [row["kind"] for row in repository.outbox_rows(["PENDING"])]
    assert kinds == ["signal", "reply"]

    telegram.send_message.side_effect = [500, 501]
    OutboxFlusher(repository, client_factory=lambda: telegram).flush()
    assert repository.get_signal_message_id("s1") == 500
    assert telegram.send_message.call_args_list[-1].kwargs["reply_to_message_id"] == 500
    repository.close()


# --- Shadow ideas ------------------------------------------------------------------

def test_blocked_idea_is_followed_to_its_result(tmp_path: Path) -> None:
    from src.core.orchestrator import PulseOrchestrator

    repository = _repo(tmp_path)
    repository.log_setup(
        symbol="XAUUSD", strategy="PIN_BAR_REJECTION", direction="LONG", order_type="LIMIT",
        score=0, classification="BLOCKED", vetoes="Blocked: test", timestamp=T0,
        levels=(2400.0, 2395.0, 2407.5, 2415.0),
    )
    orchestrator = PulseOrchestrator()
    orchestrator._update_shadow_outcomes(repository, "XAUUSD", [
        _candle(300, 2401.0, 2399.0, 2400.5),   # fills
        _candle(600, 2408.0, 2401.0, 2407.8),   # TP1
        _candle(900, 2416.0, 2407.0, 2415.5),   # TP2
    ])
    status, realized = repository.query("SELECT shadow_status, shadow_r FROM setup_log;")[0]
    assert status == "CLOSED_TP2"
    assert realized == 2.25
    repository.close()


# --- Dashboard login ---------------------------------------------------------------

def test_dashboard_login_comes_from_environment(monkeypatch) -> None:
    from src.dashboard import auth

    monkeypatch.delenv("DASHBOARD_USERNAME", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)
    assert auth.login_configured() is False
    assert auth.verify_credentials("Machete", "@Machete1231") is False

    monkeypatch.setenv("DASHBOARD_USERNAME", "owner")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "long-secret")
    assert auth.verify_credentials("owner", "long-secret") is True
    assert auth.verify_credentials("owner", "wrong") is False
    assert auth.load_user("owner") is not None
