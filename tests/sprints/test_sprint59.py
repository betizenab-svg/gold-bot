"""Sprint 59 (roadmap milestone 4): weekly loss brake, the same-dollar-bet cap,
the spread cushion, tested targets and entries, lot-size warnings, news for
open trades, the Friday weekend plan and the monthly risk review."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import settings
from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.analysis.evidence import cost_in_r
from src.analysis.outcomes import realized_r_for_event
from src.analysis.position_sizing import LotSizeCalculator
from src.analysis.risk_governor import RiskGovernor, week_start
from src.analysis.signal_factory import SignalFactory
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer

WEDNESDAY = 1790164800  # Wed 2026-09-23 12:00 UTC
FRIDAY_2030 = 1790368200  # Fri 2026-09-25 20:30 UTC (25 min before the weekly close)
FRIDAY_2000 = FRIDAY_2030 - 1800
MONDAY_0800 = 1790582400  # Mon 2026-09-28 08:00 UTC


def _repo(tmp_path: Path) -> Repository:
    connection = sqlite3.connect(str(tmp_path / "m4.db"))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


def _signal(symbol: str = "XAUUSD", direction: str = "LONG", **overrides) -> Signal:
    values = dict(
        symbol=symbol, signal_type=direction, entry_price=2400.0,
        sl_price=2395.0 if direction == "LONG" else 2405.0,
        tp1_price=2407.5 if direction == "LONG" else 2392.5,
        tp2_price=2415.0 if direction == "LONG" else 2385.0,
        score=80, reasoning="test", timestamp=FRIDAY_2000 - 3600,
        signal_hash=f"h-{symbol}-{direction}", strategy="PIN_BAR",
    )
    values.update(overrides)
    return Signal(**values)


def _governor_repo(open_signals=None, closed=None, kv=None) -> MagicMock:
    repository = MagicMock()
    store = dict(kv or {})
    repository.get_kv.side_effect = lambda key: store.get(key)
    repository.get_open_signals.return_value = list(open_signals or [])
    repository.count_signals_since.return_value = 0
    repository.get_closed_results_since.return_value = list(closed or [])
    return repository


def _candle(ts: int, high: float, low: float, close: float, symbol: str = "XAUUSD") -> Candle:
    return Candle(symbol, "M5", ts, (high + low) / 2, high, low, close, 0.0)


# --- Risk brakes ------------------------------------------------------------------

def test_week_starts_on_monday_utc() -> None:
    assert week_start(WEDNESDAY) == WEDNESDAY - 2 * 86400 - 12 * 3600


def test_weekly_loss_brake() -> None:
    governor = RiskGovernor()
    losing_week = [("PIN_BAR", "XAUUSD", -1.0)] * 6
    allowed, reason = governor.is_trading_allowed(_governor_repo(closed=losing_week), WEDNESDAY, "XAUUSD", "LONG")
    assert not allowed and "weekly loss brake" in reason
    allowed, _ = governor.is_trading_allowed(_governor_repo(closed=losing_week[:5]), WEDNESDAY, "XAUUSD", "LONG")
    assert allowed


def test_gold_euro_and_pound_count_as_one_dollar_bet(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RISK_MAX_SAME_USD_BET", 1)
    governor = RiskGovernor()
    repository = _governor_repo(open_signals=[_signal("XAUUSD", "LONG")])
    allowed, reason = governor.is_trading_allowed(repository, WEDNESDAY, "EURUSD", "LONG")
    assert not allowed and "same dollar bet" in reason
    assert governor.is_trading_allowed(repository, WEDNESDAY, "EURUSD", "SHORT")[0]
    assert governor.is_trading_allowed(repository, WEDNESDAY, "BTCUSD", "LONG")[0]
    trial_only = _governor_repo(open_signals=[_signal("XAUUSD", "LONG", trial=True)])
    assert governor.is_trading_allowed(trial_only, WEDNESDAY, "EURUSD", "LONG")[0]


def test_news_pause_follows_each_markets_currencies() -> None:
    governor = RiskGovernor()
    euro_news = json.dumps([{"timestamp": WEDNESDAY + 600, "label": "ECB", "currency": "EUR"}])
    repository = _governor_repo(kv={"upcoming_news_events_json": euro_news})
    allowed, reason = governor.is_trading_allowed(repository, WEDNESDAY, "EURUSD", "LONG")
    assert not allowed and "news" in reason
    assert governor.is_trading_allowed(repository, WEDNESDAY, "XAUUSD", "LONG")[0]
    owner_added = json.dumps([{"timestamp": WEDNESDAY + 600, "label": "manual", "manual": True}])
    assert not governor.is_trading_allowed(
        _governor_repo(kv={"upcoming_news_events_json": owner_added}), WEDNESDAY, "XAUUSD", "LONG"
    )[0]


def test_no_new_trades_just_before_the_weekend(monkeypatch) -> None:
    governor = RiskGovernor()
    monkeypatch.setattr(settings, "WEEKEND_ACTION", "close")
    allowed, reason = governor.is_trading_allowed(_governor_repo(), FRIDAY_2000, "XAUUSD", "LONG")
    assert not allowed and "weekend" in reason
    assert governor.is_trading_allowed(_governor_repo(), FRIDAY_2000, "BTCUSD", "LONG")[0]
    monkeypatch.setattr(settings, "WEEKEND_ACTION", "off")
    assert governor.is_trading_allowed(_governor_repo(), FRIDAY_2000, "XAUUSD", "LONG")[0]


# --- Prices: spread cushion, targets, entries -------------------------------------------

def test_spread_cushion_moves_only_ask_side_prices(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SPREAD_CUSHION_ENABLED", True)
    factory = SignalFactory()
    zone = {"id": 1, "price_top": 2000.0, "price_bottom": 1990.0}
    entry, sl, tp1, tp2 = factory.calculate_parameters("LONG", zone, atr=4.0)
    assert entry == pytest.approx(2000.30) and sl == pytest.approx(1984.0)
    assert tp1 == pytest.approx(2024.0)  # targets stay at the chart levels
    entry, sl, tp1, tp2 = factory.calculate_parameters("SHORT", zone, atr=4.0)
    assert entry == pytest.approx(1990.0)
    assert sl == pytest.approx(2006.30) and tp1 == pytest.approx(1966.30)


def test_fills_follow_the_ask_when_the_cushion_is_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SPREAD_CUSHION_ENABLED", True)
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    pending = {"signal_type": "LONG", "entry_price": 2000.30, "sl_price": 1984.0, "tp1_price": 2024.0,
               "tp2_price": 2048.0, "status": "PENDING", "order_type": "LIMIT", "symbol": "XAUUSD",
               "timestamp": WEDNESDAY}
    assert manager.evaluate_signal(pending, _candle(WEDNESDAY + 300, 2003.0, 2000.10, 2002.0)) is None
    assert manager.evaluate_signal(pending, _candle(WEDNESDAY + 300, 2003.0, 1999.95, 2002.0)) == "ACTIVATED"
    short = {"signal_type": "SHORT", "entry_price": 1990.0, "sl_price": 2006.30, "tp1_price": 1966.30,
             "tp2_price": 1942.30, "status": "ACTIVE", "symbol": "XAUUSD", "timestamp": WEDNESDAY}
    assert manager.evaluate_signal(short, _candle(WEDNESDAY + 300, 2006.05, 2001.0, 2004.0)) == "SL_HIT"


def test_target_settings_and_exact_results(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TP1_R", 1.0)
    monkeypatch.setattr(settings, "TP2_R", 2.0)
    entry, sl, tp1, tp2 = SignalFactory().calculate_parameters(
        "LONG", {"entry_price": 100.0, "sl_price": 96.0}, atr=1.0
    )
    assert (tp1, tp2) == (104.0, 108.0)
    assert realized_r_for_event("BE_HIT", "LONG", 100, 95, 110, tp1=105) == 0.5
    assert realized_r_for_event("TP2_SMASH", "LONG", 100, 95, 110, tp1=105) == 1.5
    assert realized_r_for_event("BE_HIT", "LONG", 100, 95, 110) == 0.75  # older rows


def test_market_entry_starts_active_and_does_not_refire(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTRY_MODE", "market")
    setup = {"entry_price": 100.0, "sl_price": 96.0, "strategy": "PIN_BAR", "market_price": 103.0}
    first = SignalFactory().build_signal("XAUUSD", "LONG", dict(setup), 1.0, 80, WEDNESDAY)
    assert (first.entry_price, first.status, first.order_type) == (103.0, "ACTIVE", "MARKET")
    assert first.sl_price == 96.0 and first.tp1_price == pytest.approx(113.5)
    later = SignalFactory().build_signal("XAUUSD", "LONG", dict(setup, market_price=104.0), 1.0, 80, WEDNESDAY + 300)
    assert later.signal_hash == first.signal_hash
    monkeypatch.setattr(settings, "ENTRY_MODE", "limit")
    normal = SignalFactory().build_signal("XAUUSD", "LONG", dict(setup), 1.0, 80, WEDNESDAY)
    assert (normal.entry_price, normal.status) == (100.0, "PENDING")


def test_costs_are_not_counted_twice_with_the_cushion() -> None:
    assert cost_in_r("XAUUSD", 2400.0, 2395.0) == pytest.approx(0.1)
    assert cost_in_r("XAUUSD", 2400.0, 2395.0, spread_included=True) == pytest.approx(0.04)


# --- Lot sizes ------------------------------------------------------------------------

def test_small_accounts_are_warned_when_the_smallest_lot_is_too_big() -> None:
    calculator = LotSizeCalculator()
    rows = calculator.quick_lots(2400.0, 2390.0, 0.01, "XAUUSD")  # 100 pip stop
    assert [r["balance"] for r in rows] == [100, 500, 1000]
    assert rows[0]["lot"] == 0.01 and rows[0]["too_big"] and rows[0]["risk_pct"] == 10.0
    assert not rows[2]["too_big"]
    table = calculator.generate_table(2400.0, 2390.0, risk_pct=0.01)
    assert "\u26a0" in table and "smallest lot" in table
    assert "smallest lot" not in calculator.generate_table(2400.0, 2399.0, risk_pct=0.01, symbol="XAUUSD").split(
        "$5000"
    )[1]


# --- Friday weekend plan ----------------------------------------------------------------

def test_weekend_plan_closes_and_withdraws(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEEKEND_ACTION", "close")
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    candle = _candle(FRIDAY_2030, 2404.0, 2401.0, 2402.0)
    assert manager.evaluate_signal(_signal(status="PENDING"), candle) == "WEEKEND_CANCEL"
    active = _signal(status="ACTIVE")
    assert manager.evaluate_signal(active, candle) == "WEEKEND_CLOSE"
    assert manager._event_realized_r(active, "WEEKEND_CLOSE", candle) == pytest.approx(0.4)
    assert manager.evaluate_signal(_signal(status="PARTIAL_TP1"), candle) == "WEEKEND_RUNNER_CLOSE"
    assert manager.evaluate_signal(_signal("BTCUSD", status="ACTIVE"), _candle(FRIDAY_2030, 2404.0, 2401.0, 2402.0, "BTCUSD")) is None
    earlier = _candle(FRIDAY_2030 - 3600, 2404.0, 2401.0, 2402.0)
    assert manager.evaluate_signal(active, earlier) is None
    reason = manager._build_lifecycle_reason(active, "WEEKEND_CLOSE", candle)
    assert "+0.40R" in reason


def test_weekend_breakeven_plan(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEEKEND_ACTION", "breakeven")
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    winner = _signal(status="ACTIVE")
    assert manager.evaluate_signal(winner, _candle(FRIDAY_2030, 2404.0, 2401.0, 2402.0)) is None
    assert manager.evaluate_signal(winner, _candle(FRIDAY_2030, 2400.5, 2398.0, 2399.0)) == "WEEKEND_CLOSE"
    # Held into the weekend: on Monday the stop sits at entry.
    assert manager.evaluate_signal(winner, _candle(MONDAY_0800, 2401.0, 2399.5, 2400.5)) == "EARLY_BE"


# --- Owner and subscriber notices ---------------------------------------------------------

def test_open_trades_are_warned_before_their_news(tmp_path: Path) -> None:
    from src.core.orchestrator import PulseOrchestrator

    repository = _repo(tmp_path)
    euro = _signal("EURUSD", "LONG", entry_price=1.1, sl_price=1.095, tp1_price=1.1075, tp2_price=1.115,
                   signal_hash="eur1", timestamp=int(time.time()) - 600)
    repository.save_signal(euro)
    repository.update_signal_status("eur1", "ACTIVE")
    repository.update_signal_message_id("eur1", 77)
    repository.update_signal_telegram_metadata("eur1", "77", "public")
    news = [{"timestamp": int(time.time()) + 600, "label": "ECB rate decision", "currency": "EUR"}]
    repository.set_kv("upcoming_news_events_json", json.dumps(news))
    telegram = MagicMock()
    telegram.send_message.return_value = 101
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    candle = _candle(int(time.time()) - 300, 1.101, 1.099, 1.1, "EURUSD")

    orchestrator._notify_open_trades(repository, "EURUSD", candle)
    orchestrator._notify_open_trades(repository, "EURUSD", candle)
    orchestrator._notify_open_trades(repository, "XAUUSD", candle)

    assert telegram.send_message.call_count == 1
    text = telegram.send_message.call_args.args[0]
    assert "Big news in" in text and "ECB rate decision" in text and "EAT" in text
    assert telegram.send_message.call_args.kwargs["reply_to_message_id"] == 77
    repository.close()


def test_monthly_risk_review(tmp_path: Path) -> None:
    from src.alerting.owner_reports import build_monthly_risk_review, month_bounds

    repository = _repo(tmp_path)
    start, _end = month_bounds(2026, 9)
    results = [-1.0, -1.0, 2.25, -1.4, 0.75]
    for index, value in enumerate(results):
        repository.save_signal(_signal(signal_hash=f"m{index}", timestamp=start + index * 86400))
        repository.update_signal_closure(
            f"m{index}", "closed", "CLOSED_SL" if value < 0 else "CLOSED_TP2",
            realized_r=value, closed_at=start + index * 86400 + 3600,
        )
    text = build_monthly_risk_review(repository, 2026, 9)
    assert "September 2026" in text and "-0.40R" in text
    assert "Longest losing streak: 2" in text
    assert "0.40R worse than the planned 1R" in text
    assert "No finished trades" in build_monthly_risk_review(repository, 2026, 8)
    repository.close()


def test_history_runs_try_targets_entries_and_waiting_times() -> None:
    from scripts.history.run_all import jobs

    variants = {variant for _symbol, variant, *_ in jobs(["EURUSD"], "2024-01", "2024-12")}
    assert {"tp1_1.0", "tp2_2.0", "entry_market", "expiry_180", "be1.0"} <= variants
