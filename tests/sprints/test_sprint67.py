"""Sprint 67: the Gold 4-hour System (src/strategies/gold_system.py) wired into
the live bot: triggers, system rules, fixed single-target plans, routing past
the old scoring, 5-minute trade watching, gold-only defaults and history."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import settings as app_settings
from src.alerting.formatter import SignalFormatter
from src.alerting.i18n import STRINGS
from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.analysis.outcomes import realized_r_for_event
from src.analysis.signal_factory import SignalFactory
from src.analysis.trade_windows import max_hold_seconds
from src.core.orchestrator import GOLD_SYSTEM_SCORE, PulseOrchestrator
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.strategies import gold_system
from src.strategies.gold_system import BREAKOUT, PULLBACK, SQUEEZE

H4 = 4 * 3600
T0 = int(datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp())  # a Monday


def _repo(tmp_path: Path, name: str = "gold_system.db") -> Repository:
    connection = sqlite3.connect(str(tmp_path / name))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


def _flat(count: int, symbol: str = "XAUUSD_H4", timeframe: str = "H4") -> list[Candle]:
    return [
        Candle(symbol, timeframe, T0 + H4 * i, 4000.0, 4005.0, 3995.0, 4000.0 + (i % 2), 0)
        for i in range(count)
    ]


def _with_breakout(candles: list[Candle]) -> list[Candle]:
    last = candles[-1]
    return candles + [Candle(last.symbol, last.timeframe, last.timestamp + H4, 4000.0, 4030.0, 3998.0, 4025.0, 0)]


def _telegram() -> MagicMock:
    sent: list[str] = []
    telegram = MagicMock()
    telegram.chat_id = "test-chat"
    telegram.send_message.side_effect = lambda text, **kw: sent.append(text) or len(sent)
    telegram.sent = sent
    return telegram


# --- Triggers -----------------------------------------------------------------------------

def test_breakout_fires_with_the_tested_levels() -> None:
    candles = _with_breakout(_flat(40))
    setups = gold_system.detect_all(candles)
    assert [s["strategy"] for s in setups] == [BREAKOUT]
    setup = setups[0]
    atr = gold_system._atr(candles)
    assert setup["trade_direction"] == "LONG" and setup["order_type"] == "MARKET" and setup["fixed_plan"]
    assert setup["entry_price"] == 4025.0
    assert setup["sl_price"] == pytest.approx(4025.0 - 1.5 * atr, abs=0.01)
    assert setup["tp_price"] == pytest.approx(4025.0 + 1.5 * atr, abs=0.01)  # one target at 1R
    assert setup["hold_candles"] == 30
    # Only gold's 4-hour chart runs the system.
    assert gold_system.detect_all(_with_breakout(_flat(40, "EURUSD_H1", "H1"))) == []
    assert gold_system.detect_all(_with_breakout(_flat(40, "XAUUSD", "M5"))) == []
    assert gold_system.detect_all(_flat(40)) == []


def test_squeeze_needs_a_quiet_spell_then_a_close_outside_the_band() -> None:
    wild = [
        Candle("XAUUSD_H4", "H4", T0 + H4 * i, 4000.0, 4060.0, 3940.0, 4000.0 + (40 if i % 2 else -40), 0)
        for i in range(60)
    ]
    calm = [
        Candle("XAUUSD_H4", "H4", T0 + H4 * (60 + i), 4000.0, 4002.0, 3998.0, 4000.0 + (i % 2), 0)
        for i in range(30)
    ]
    up = Candle("XAUUSD_H4", "H4", T0 + H4 * 90, 4000.0, 4012.0, 3999.0, 4010.0, 0)
    assert gold_system.squeeze(wild + calm + [up]) == 1
    down = Candle("XAUUSD_H4", "H4", T0 + H4 * 90, 4000.0, 4001.0, 3988.0, 3990.0, 0)
    assert gold_system.squeeze(wild + calm + [down]) == -1
    # Too few candles, or no quiet spell: nothing.
    assert gold_system.squeeze((wild + calm + [up])[-60:]) == 0
    loud = wild + wild[:30]
    loud = [Candle(c.symbol, c.timeframe, T0 + H4 * i, c.open, c.high, c.low, c.close, 0) for i, c in enumerate(loud)]
    assert gold_system.squeeze(loud + [Candle("XAUUSD_H4", "H4", T0 + H4 * 90, 4000, 4100, 3990, 4090.0, 0)]) == 0


def test_pullback_buys_a_dip_to_the_20_average_in_an_uptrend() -> None:
    rising = [
        Candle("XAUUSD_H4", "H4", T0 + H4 * i, 3000.0 + i - 0.5, 3000.0 + i + 1, 3000.0 + i - 1, 3000.0 + i, 0)
        for i in range(260)
    ]
    previous = rising[-1].close
    dip = Candle("XAUUSD_H4", "H4", rising[-1].timestamp + H4, previous - 12, previous - 7, previous - 15, previous - 8, 0)
    assert gold_system.pullback(rising + [dip]) == 1
    assert gold_system.pullback(rising[-200:] + [dip]) == 0  # needs 250 candles
    no_dip = Candle("XAUUSD_H4", "H4", dip.timestamp, previous, previous + 3, previous - 1, previous + 2, 0)
    assert gold_system.pullback(rising + [no_dip]) == 0
    setups = gold_system.detect_all(rising + [dip])
    assert PULLBACK in [s["strategy"] for s in setups]


# --- System rules -------------------------------------------------------------------------

def _setup(strategy: str, direction: str = "LONG") -> dict:
    return {"strategy": strategy, "trade_direction": direction}


def test_system_rules_one_per_candle_quiet_periods_and_open_trade_caps() -> None:
    never = {name: None for name in gold_system.PLANS}
    chosen, fired, skipped = gold_system.choose([_setup(BREAKOUT), _setup(SQUEEZE)], [], never)
    assert chosen["strategy"] == BREAKOUT and fired == [BREAKOUT, SQUEEZE]
    assert skipped and "one signal per candle" in skipped[0][1]

    quiet = dict(never, **{BREAKOUT: 9})  # breakout waits 10 candles after it fires
    chosen, fired, _ = gold_system.choose([_setup(BREAKOUT)], [], quiet)
    assert chosen is None and fired == []
    chosen, _, _ = gold_system.choose([_setup(BREAKOUT)], [], dict(never, **{BREAKOUT: 10}))
    assert chosen is not None

    chosen, fired, skipped = gold_system.choose([_setup(BREAKOUT)], [(BREAKOUT, "LONG")], never)
    assert chosen is None and fired == [BREAKOUT] and "already open" in skipped[0][1]
    chosen, _, skipped = gold_system.choose([_setup(PULLBACK)], [(BREAKOUT, "LONG"), (SQUEEZE, "LONG")], never)
    assert chosen is None and "2 LONG trades already open" in skipped[0][1]
    chosen, _, _ = gold_system.choose([_setup(PULLBACK, "SHORT")], [(BREAKOUT, "LONG"), (SQUEEZE, "LONG")], never)
    assert chosen is not None


# --- Signal, card, lifecycle --------------------------------------------------------------

def _system_signal(monkeypatch=None, cushion: bool = False):
    if monkeypatch is not None:
        monkeypatch.setattr(app_settings, "SPREAD_CUSHION_ENABLED", cushion)
    setup = gold_system.detect_all(_with_breakout(_flat(40)))[0]
    context = PulseOrchestrator._signal_context(setup)
    context["market_price"] = setup["entry_price"]
    signal = SignalFactory().build_signal("XAUUSD_H4", "LONG", context, 11.0, GOLD_SYSTEM_SCORE, setup["timestamp"])
    return setup, signal


def test_fixed_plan_signal_keeps_its_levels_and_opens_at_market(monkeypatch) -> None:
    setup, signal = _system_signal(monkeypatch)
    assert (signal.entry_price, signal.sl_price) == (setup["entry_price"], setup["sl_price"])
    assert signal.tp1_price == signal.tp2_price == setup["tp_price"]
    assert (signal.order_type, signal.status, signal.strategy) == ("MARKET", "ACTIVE", BREAKOUT)
    assert "GOLD 4-HOUR SYSTEM" in signal.reasoning and "no half-close" in signal.reasoning
    assert "120h" in signal.reasoning
    # With the broker spread on, a buy fills one spread higher; the stop and target stay.
    _, cushioned = _system_signal(monkeypatch, cushion=True)
    assert cushioned.entry_price == pytest.approx(setup["entry_price"] + 0.3, abs=0.001)
    assert (cushioned.sl_price, cushioned.tp1_price) == (setup["sl_price"], setup["tp_price"])


def test_single_target_card_in_plain_words(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_LANGUAGE", "en")
    _, signal = _system_signal(monkeypatch)
    card = SignalFormatter().format_initial_signal(signal)
    assert "Target @" in card and "close the whole trade" in card
    assert "Target 1" not in card and "Target 2" not in card and "close half" not in card
    assert "Reward to risk: 1.0 : 1" in card and "within 120 hours" in card
    assert "Buy now at the market price" in card
    assert "highest price of the last 30 candles" in card
    for strategy in gold_system.PLANS:
        for direction in ("LONG", "SHORT"):
            english, amharic = STRINGS[f"why_{strategy}_{direction}"]
            assert english and amharic


def test_fixed_plans_never_move_the_stop_and_use_their_time_limit(monkeypatch) -> None:
    _, signal = _system_signal(monkeypatch)
    trade = {
        "signal_hash": signal.signal_hash, "symbol": "XAUUSD_H4", "strategy": BREAKOUT,
        "signal_type": "LONG", "status": "ACTIVE", "order_type": "MARKET",
        "entry_price": signal.entry_price, "sl_price": signal.sl_price,
        "tp1_price": signal.tp1_price, "tp2_price": signal.tp2_price,
        "timestamp": signal.timestamp, "mfe_r": 0.95,
    }
    manager = SignalLifecycleManager(telegram_client=MagicMock(), repository=None)
    back_to_entry = Candle("XAUUSD", "M5", signal.timestamp + H4 + 600, 4026, 4030, signal.entry_price - 1, 4027, 0)
    assert manager.evaluate_signal(trade, back_to_entry) is None  # no break-even exit
    stop = Candle("XAUUSD", "M5", signal.timestamp + H4 + 900, 4010, 4012, signal.sl_price - 0.5, 4008, 0)
    assert manager.evaluate_signal(trade, stop) == "SL_HIT"
    target = Candle("XAUUSD", "M5", signal.timestamp + H4 + 900, 4040, signal.tp2_price + 0.1, 4039, 4042, 0)
    assert manager.evaluate_signal(trade, target) == "TP2_SMASH"
    assert realized_r_for_event(
        "TP2_SMASH", "LONG", signal.entry_price, signal.sl_price, signal.tp2_price, tp1=signal.tp1_price
    ) == pytest.approx(1.0)
    assert manager._display_type(trade, "TP2_SMASH") == "TARGET_HIT"
    assert "Target Hit" in SignalFormatter().format_lifecycle_update("TARGET_HIT", "x")[0]

    # 30 candles after the signal candle closes, the trade is closed at market.
    assert max_hold_seconds("XAUUSD_H4", BREAKOUT) == 31 * H4
    assert max_hold_seconds("XAUUSD_H4", PULLBACK) == 25 * H4
    assert max_hold_seconds("XAUUSD_H4") == 96 * 3600  # older strategies unchanged
    # Weekend hours do not count, so stay inside one week: a pullback trade from
    # Monday 00:00 (24 candles) is closed at market from Friday 04:00.
    monday = dict(trade, strategy=PULLBACK, timestamp=T0, mfe_r=0.0)
    late = Candle("XAUUSD", "M5", T0 + 25 * H4 + 300, 4026, 4027, 4024, 4026, 0)
    assert manager.evaluate_signal(dict(monday, timestamp=T0 + 600), late) is None
    assert manager.evaluate_signal(monday, late) == "TIME_STOP"
    assert "time limit passed" in manager._build_lifecycle_reason(monday, "TIME_STOP", late)


# --- Live routing -------------------------------------------------------------------------

def test_system_chart_bypasses_scoring_but_the_governor_has_the_last_word(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    telegram = _telegram()
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    candles = _with_breakout(_flat(100))  # breakout and squeeze both fire
    assert orchestrator._run_gold_system(repository, None, "XAUUSD_H4", candles[-1], candles) == (1, 0)
    open_signals = repository.get_open_signals()
    assert len(open_signals) == 1
    signal = open_signals[0]
    assert (signal.strategy, signal.signal_type, signal.status, signal.order_type) == (BREAKOUT, "LONG", "ACTIVE", "MARKET")
    assert signal.score == GOLD_SYSTEM_SCORE and not signal.trial
    assert telegram.sent and "Gold 4-hour system" in telegram.sent[0]
    setups = repository.get_recent_setups(10)
    assert any(s["strategy"] == SQUEEZE and "one signal per candle" in str(s.get("vetoes")) for s in setups)

    # Next candle breaks out again: the breakout is in its quiet period, the squeeze too.
    more = _with_breakout(candles)
    more[-1] = Candle("XAUUSD_H4", "H4", more[-1].timestamp, 4025, 4060, 4024, 4055.0, 0)
    assert orchestrator._run_gold_system(repository, None, "XAUUSD_H4", more[-1], more) == (0, 0)

    # The owner's pause switch still blocks the system.
    fresh = _repo(tmp_path, "paused.db")
    fresh.set_kv("trading_paused", "1")
    assert orchestrator._run_gold_system(fresh, None, "XAUUSD_H4", candles[-1], candles) == (0, 0)
    assert fresh.get_open_signals() == []
    assert any("paused" in str(s.get("vetoes")) for s in fresh.get_recent_setups(10))
    repository.close()
    fresh.close()


def test_no_system_signal_in_the_last_two_hours_before_the_weekend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_settings, "WEEKEND_ACTION", "close")
    repository = _repo(tmp_path)
    orchestrator = PulseOrchestrator(telegram_client_factory=_telegram)
    friday_16 = int(datetime(2026, 9, 11, 16, tzinfo=timezone.utc).timestamp())  # closes 20:00 UTC
    candles = _with_breakout(_flat(40))
    shift = friday_16 - candles[-1].timestamp
    candles = [Candle(c.symbol, c.timeframe, c.timestamp + shift, c.open, c.high, c.low, c.close, 0) for c in candles]
    assert orchestrator._run_gold_system(repository, None, "XAUUSD_H4", candles[-1], candles) == (0, 0)
    assert repository.get_kv("gold_system_fired_GOLD_BREAKOUT_XAUUSD_H4") is None
    repository.close()


def test_system_trades_are_watched_on_five_minute_gold() -> None:
    match = PulseOrchestrator._signal_symbol_matches
    system_trade = MagicMock(symbol="XAUUSD_H4", strategy=BREAKOUT)
    old_trade = MagicMock(symbol="XAUUSD_H4", strategy="ENGULFING_ZONE")
    assert match(system_trade, "XAUUSD") and match(system_trade, "XAUUSD_H4")
    assert not match(old_trade, "XAUUSD") and not match(system_trade, "EURUSD")


def test_gold_system_only_turns_the_other_charts_into_price_feeds(tmp_path: Path, monkeypatch) -> None:
    from src.validation.validator import DataValidator

    monkeypatch.setattr(app_settings, "GOLD_SYSTEM_ONLY", True)
    repository = _repo(tmp_path)
    orchestrator = PulseOrchestrator(telegram_client_factory=_telegram)

    def no_detection(*args, **kwargs):
        raise AssertionError("old strategies must not run")

    monkeypatch.setattr(orchestrator, "_detect_trade_setup", no_detection)

    class Feed:
        last_source: dict = {}

        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            return [Candle("XAUUSD", "M5", T0 + 300 * i, 4000, 4002, 3998, 4001, 0) for i in range(30)]

    assert orchestrator._pulse_symbol(repository, Feed(), DataValidator(), "XAUUSD", "M5") == (0, 0)
    repository.close()


def test_four_hour_history_is_loaded_once(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    orchestrator = PulseOrchestrator(telegram_client_factory=_telegram)
    stored = _flat(31)
    repository.save_candles(stored)
    calls: list[int] = []

    class Feed:
        def fetch_history(self, symbol: str, timeframe: str, since: int) -> list[Candle]:
            calls.append(since)
            return [Candle("XAUUSD_H4", "H4", T0 - H4 * (700 - i), 3900, 3905, 3895, 3900, 0) for i in range(700)] + stored

    loaded = orchestrator._gold_system_history(repository, Feed(), "XAUUSD_H4", "H4", stored)
    assert len(loaded) == app_settings.ANALYSIS_LOOKBACK_CANDLES and loaded[-1].timestamp == stored[-1].timestamp
    assert orchestrator._gold_system_history(repository, Feed(), "XAUUSD_H4", "H4", stored[-20:]) == stored[-20:]
    assert len(calls) == 1
    repository.close()


def test_pruning_keeps_four_hour_candles_longer(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    old = T0 - 100 * 86400
    repository.save_candles([
        Candle("XAUUSD", "M5", old, 1, 1, 1, 1, 0),
        Candle("XAUUSD_H4", "H4", old, 1, 1, 1, 1, 0),
        Candle("XAUUSD", "M5", T0, 1, 1, 1, 1, 0),
    ])
    repository.prune_market_data(45, 200)
    assert len(repository.get_recent_candles("XAUUSD", "M5", 10)) == 1
    assert len(repository.get_recent_candles("XAUUSD_H4", "H4", 10)) == 1
    repository.prune_market_data(45)
    assert repository.get_recent_candles("XAUUSD_H4", "H4", 10) == []
    repository.close()
