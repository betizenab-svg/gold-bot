"""Sprint 49 — adversarial-review fixes: same-candle stop, weekend-aware
clocks, DST sessions, deterministic hashes, heartbeat, control-room detail."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.analysis.pivots import gold_session_start
from src.analysis.sessions import SessionEngine
from src.analysis.signal_factory import SignalFactory
from src.domain.candle import Candle
from src.domain.signal import Signal


def _candle(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="XAUUSD", timeframe="M5", timestamp=ts,
        open=o, high=h, low=l, close=c, volume=100.0,
    )


def _signal(**overrides) -> Signal:
    base = dict(
        symbol="XAUUSD", signal_type="LONG", entry_price=2000.0, sl_price=1994.0,
        tp1_price=2009.0, tp2_price=2018.0, score=85, reasoning="t",
        timestamp=1_700_000_000, signal_hash="s49", status="PENDING",
        order_type="LIMIT",
    )
    base.update(overrides)
    return Signal(**base)


# --- Same-candle fill + stop-out (HIGH defect fix) ----------------------------

def test_limit_fill_and_stop_same_candle_is_sl_hit() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(order_type="LIMIT")  # buy limit 2000, SL 1994
    crash = _candle(1_700_000_300, 2003.0, 2004.0, 1992.0, 1993.0)
    assert manager.evaluate_signal(signal, crash) == "SL_HIT"


def test_stop_fill_and_stop_out_same_candle_short() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(
        signal_type="SHORT", order_type="STOP",
        entry_price=1990.0, sl_price=1996.0,
        tp1_price=1981.0, tp2_price=1972.0,
    )
    whipsaw = _candle(1_700_000_300, 1993.0, 1997.0, 1989.0, 1996.5)
    assert manager.evaluate_signal(signal, whipsaw) == "SL_HIT"


def test_clean_fill_still_activates() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(order_type="LIMIT")
    pullback = _candle(1_700_000_300, 2002.0, 2003.0, 1999.5, 2001.0)
    assert manager.evaluate_signal(signal, pullback) == "ACTIVATED"


# --- Weekend-aware clocks -------------------------------------------------------

FRIDAY_18UTC = 1_700_244_000   # Fri 2023-11-17 18:00 UTC
MONDAY_10UTC = 1_700_474_400   # Mon 2023-11-20 10:00 UTC


def test_active_trade_survives_weekend() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(status="ACTIVE", timestamp=FRIDAY_18UTC)
    monday_candle = _candle(MONDAY_10UTC, 2001.0, 2002.0, 2000.0, 2001.5)
    # 64 wall-clock hours, but only ~16 trading hours: no time stop.
    assert manager.evaluate_signal(signal, monday_candle) is None


def test_trading_age_subtracts_weekend() -> None:
    age = SignalLifecycleManager._trading_age_seconds(FRIDAY_18UTC, MONDAY_10UTC)
    assert age == 16 * 3600


# --- DST-aware sessions ----------------------------------------------------------

def test_london_killzone_follows_new_york_clock() -> None:
    engine = SessionEngine()
    # Winter (EST): 2023-11-14 08:00 UTC = 03:00 NY -> killzone.
    assert engine.classify_session(1_699_948_800) == "LONDON_KILLZONE"
    # Summer (EDT): 2026-07-15 06:30 UTC = 02:30 NY -> killzone.
    # Fixed-UTC logic (old 07-10 window) would have missed this.
    import calendar
    summer_ts = calendar.timegm((2026, 7, 15, 6, 30, 0))
    assert engine.classify_session(summer_ts) == "LONDON_KILLZONE"


def test_gold_session_start_rolls_at_5pm_ny() -> None:
    import calendar
    # 2023-11-14 20:00 UTC = 15:00 EST -> session started Nov 13 17:00 EST (22:00 UTC).
    before_close = calendar.timegm((2023, 11, 14, 20, 0, 0))
    assert gold_session_start(before_close) == calendar.timegm((2023, 11, 13, 22, 0, 0))
    # 2023-11-14 23:30 UTC = 18:30 EST -> new session started Nov 14 22:00 UTC.
    after_close = calendar.timegm((2023, 11, 14, 23, 30, 0))
    assert gold_session_start(after_close) == calendar.timegm((2023, 11, 14, 22, 0, 0))


# --- Deterministic signal hash -----------------------------------------------------

def test_hash_depends_on_candle_time_not_wall_clock() -> None:
    factory = SignalFactory()
    kwargs = dict(
        symbol="XAUUSD", trade_direction="LONG",
        zone_dict={"id": 5, "price_top": 2005.0, "price_bottom": 2000.0},
        atr=3.0, score=80,
    )
    first = factory.build_signal(timestamp=1_700_000_000, **kwargs)
    second = factory.build_signal(timestamp=1_700_000_300, **kwargs)  # same UTC day
    assert first.signal_hash == second.signal_hash

    next_day = factory.build_signal(timestamp=1_700_000_000 + 86400, **kwargs)
    assert next_day.signal_hash != first.signal_hash


# --- Pulse health heartbeat ----------------------------------------------------------

class _KvRepo:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get_kv(self, key: str):
        return self.kv.get(key)

    def set_kv(self, key: str, value) -> None:
        self.kv[key] = str(value)


def test_heartbeat_and_error_alert() -> None:
    from src.core.orchestrator import PulseOrchestrator

    telegram = MagicMock()
    telegram.chat_id = "chat-1"
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    repo = _KvRepo()

    orchestrator._record_pulse_health(repo, errors_encountered=0)
    assert repo.kv["consecutive_pulse_errors"] == "0"
    assert "last_pulse_wallclock" in repo.kv

    for _ in range(5):
        orchestrator._record_pulse_health(repo, errors_encountered=1)
    telegram.send_message.assert_called_once()
    assert "health alert" in telegram.send_message.call_args.args[0]

    # Cooldown: a sixth failing pulse does not re-alert.
    orchestrator._record_pulse_health(repo, errors_encountered=1)
    telegram.send_message.assert_called_once()


# --- Control room: signal detail ---------------------------------------------------------

@pytest.fixture
def dashboard_client(tmp_path, monkeypatch):
    import importlib

    from src.persistence.schema import SchemaInitializer

    dashboard_app = importlib.import_module("src.dashboard.app")

    db_path = tmp_path / "dash49.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    connection.execute(
        """
        INSERT INTO signals (signal_hash, symbol, signal_type, entry_price, sl_price,
                             tp1_price, tp2_price, score, reasoning, timestamp,
                             status, order_type, strategy)
        VALUES ('detail-1', 'XAUUSD', 'LONG', 2000, 1994, 2009, 2018, 88,
                'TRADE PLAN | test', 1700000000, 'ACTIVE', 'STOP', 'PIN_BAR_REJECTION');
        """
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(dashboard_app, "DB_PATH", str(db_path))
    flask_app = dashboard_app.create_app()
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    client.post(
        "/login",
        data={"username": "Machete", "password": "@Machete1231"},
        follow_redirects=True,
    )
    return client


def test_signal_detail_page_renders(dashboard_client) -> None:
    response = dashboard_client.get("/signals/detail-1")
    assert response.status_code == 200
    assert b"TRADE PLAN" in response.data


def test_signal_chart_404_without_candles(dashboard_client) -> None:
    response = dashboard_client.get("/signals/detail-1/chart.png")
    assert response.status_code == 404


def main() -> None:
    print("Sprint 49 verified")


if __name__ == "__main__":
    main()
