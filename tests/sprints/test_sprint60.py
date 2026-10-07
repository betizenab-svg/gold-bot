"""Sprint 60 (roadmap milestone 5): trial mode, new markets, swing charts,
session range breakouts and the one-way-day filter."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import scripts.history.sources as sources
from config import instruments
from config.instruments import INSTRUMENTS, active_symbols, history_key, is_trial
from src.analysis.market_hours import market_open, minutes_to_weekly_close
from src.analysis.risk_governor import RiskGovernor
from src.analysis.trend_day import against_trend_day, trend_day_direction
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.ingestion.factory import MarketDataRouter
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.strategies.inside_bar_trap import InsideBarTrapStrategy
from src.strategies.range_breakout import AsianRangeBreakoutStrategy, OpeningRangeBreakoutStrategy


def _ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def _repo(tmp_path: Path) -> Repository:
    connection = sqlite3.connect(str(tmp_path / "m5.db"))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


def _bar(symbol: str, ts: int, high: float, low: float, close: float, open_: float | None = None,
         timeframe: str = "M5") -> Candle:
    return Candle(symbol, timeframe, ts, open_ if open_ is not None else (high + low) / 2, high, low, close, 0.0)


# --- Markets ----------------------------------------------------------------------------

def test_new_markets_and_swing_charts_are_registered(monkeypatch) -> None:
    monkeypatch.delenv("SYMBOLS", raising=False)
    # Gold only by default (owner's decision); the other markets stay registered
    # for history tests and can be switched back on with the SYMBOLS variable.
    assert active_symbols() == ["XAUUSD", "XAUUSD_H4"]
    for symbol in ("US100", "US500", "USDJPY", "AUDUSD", "ETHUSD", "XAUUSD_H1", "XAUUSD_H4", "EURUSD_H1"):
        assert symbol in INSTRUMENTS and INSTRUMENTS[symbol].live_feed
    # No free on-time price feed: history tests only.
    assert not INSTRUMENTS["XAGUSD"].live_feed and not INSTRUMENTS["WTIUSD"].live_feed
    assert INSTRUMENTS["XAGUSD"].history_source == "HISTDATA"
    assert history_key("XAUUSD_H4") == "XAUUSD" and INSTRUMENTS["XAUUSD_H4"].signal_timeframe == "H4"
    assert INSTRUMENTS["XAUUSD_H1"].yahoo_ticker == "GC=F" and INSTRUMENTS["XAUUSD_H1"].spot_symbol == "XAU/USD"
    assert "US30" not in INSTRUMENTS  # no free history to test the Dow on; US500 instead


def test_trial_until_promoted(monkeypatch) -> None:
    monkeypatch.delenv("PROMOTED_SYMBOLS", raising=False)
    monkeypatch.delenv("TRIAL_STRATEGIES", raising=False)
    assert is_trial("US100") and is_trial("XAUUSD_H1")
    assert not is_trial("XAUUSD", "PIN_BAR_REJECTION")
    assert is_trial("XAUUSD", "OPENING_RANGE_BREAKOUT")
    monkeypatch.setenv("PROMOTED_SYMBOLS", "US100")
    monkeypatch.setenv("TRIAL_STRATEGIES", "NONE")
    assert not is_trial("US100") and not is_trial("XAUUSD", "OPENING_RANGE_BREAKOUT")
    monkeypatch.setenv("TRIAL_STRATEGIES", "")  # an empty GitHub variable keeps the default
    assert is_trial("XAUUSD", "ASIAN_RANGE_BREAKOUT")
    assert instruments.DEFAULT_TRIAL_STRATEGIES


def test_us_indices_follow_the_new_york_cash_session() -> None:
    summer = _ts(2026, 10, 6, 13, 30)  # 09:30 New York (summer time)
    assert market_open("US100", summer) and not market_open("US100", summer - 60)
    assert not market_open("US100", _ts(2026, 10, 6, 20, 0))
    winter = _ts(2026, 12, 1, 14, 30)  # 09:30 New York (winter time)
    assert market_open("US500", winter) and not market_open("US500", winter - 60)
    assert minutes_to_weekly_close("US100", _ts(2026, 10, 9, 19, 45)) == 15
    assert market_open("XAUUSD", _ts(2026, 10, 6, 3, 0))


def test_slow_charts_only_call_the_feed_when_a_new_candle_can_exist(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    yahoo = MagicMock()
    yahoo.fetch_latest_candles.return_value = []
    router = MarketDataRouter(repository, yahoo_client=yahoo, spot_client=MagicMock())
    now = int(time.time())
    repository.set_kv("last_processed_EURUSD_H1", str(now - 3600))  # bar started an hour ago
    assert router.fetch_latest_candles("EURUSD_H1", "H1") == []
    assert router.last_source["EURUSD_H1"] == "WAIT" and yahoo.fetch_latest_candles.call_count == 0
    repository.set_kv("last_processed_EURUSD_H1", str(now - 2 * 3600 - 60))
    router.fetch_latest_candles("EURUSD_H1", "H1")
    assert yahoo.fetch_latest_candles.call_count == 1
    router.fetch_latest_candles("EURUSD", "M5")  # fast charts are never skipped
    assert yahoo.fetch_latest_candles.call_count == 2
    repository.close()


def test_one_download_feeds_every_chart_timeframe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sources, "HISTORY_DIR", tmp_path)
    start = _ts(2026, 8, 3)
    minutes = [(start + 60 * i, 1.0, 1.5, 0.5, 1.2, 0.0) for i in range(60 * 24)]
    histdata = MagicMock()
    histdata.fetch.return_value = minutes
    saved = sources.fetch_symbol(
        "XAUUSD", "HISTDATA", "XAUUSD", ["M5", "H1", "H4"], [(2026, 8)],
        histdata=histdata, now=datetime(2026, 10, 6, tzinfo=timezone.utc),
    )
    assert saved == 3
    assert len(sources.load_month("XAUUSD", "H1", 2026, 8)) == 24
    assert len(sources.load_month("XAUUSD", "H4", 2026, 8)) == 6
    # A slower chart is built from a faster month already on disk, no download.
    sources.save_month("EURUSD", "M5", 2026, 7, [(start - 86400 * 30 + 300 * i, 1, 1, 1, 1, 0) for i in range(24)])
    histdata.fetch.reset_mock()
    assert sources.fetch_symbol(
        "EURUSD", "HISTDATA", "EURUSD", ["M5", "H1"], [(2026, 7)],
        histdata=histdata, now=datetime(2026, 10, 6, tzinfo=timezone.utc),
    ) == 1
    assert histdata.fetch.call_count == 0


# --- Strategies ------------------------------------------------------------------------

def _london_morning(symbol: str = "XAUUSD") -> list[Candle]:
    start = _ts(2026, 10, 6, 5, 0)
    bars = []
    for i in range(24):  # 05:00-06:55, quiet
        ts = start + 300 * i
        bars.append(_bar(symbol, ts, 2006.5, 2003.5, 2005.0))
    for i, (high, low) in enumerate([(2008, 2002), (2010, 2004), (2008, 2002), (2007, 2000), (2008, 2003), (2007, 2002)]):
        bars.append(_bar(symbol, _ts(2026, 10, 6, 7, 0) + 300 * i, high, low, (high + low) / 2))
    for i in range(3):  # 07:30-07:40 inside the range
        bars.append(_bar(symbol, _ts(2026, 10, 6, 7, 30) + 300 * i, 2007.0, 2004.0, 2006.0))
    return bars


def test_london_opening_range_breakout() -> None:
    bars = _london_morning()
    breakout = _bar("XAUUSD", _ts(2026, 10, 6, 7, 45), 2013.0, 2006.5, 2012.0, open_=2007.0)
    setup = OpeningRangeBreakoutStrategy().detect_setup(bars + [breakout])
    assert setup["strategy"] == "OPENING_RANGE_BREAKOUT" and setup["trigger"] == "LONDON_ORB"
    assert (setup["trade_direction"], setup["entry_price"], setup["sl_price"]) == ("LONG", 2010.0, 2000.0)
    later = _bar("XAUUSD", _ts(2026, 10, 6, 7, 50), 2014.0, 2011.0, 2013.5)
    assert OpeningRangeBreakoutStrategy().detect_setup(bars + [breakout, later]) is None  # first breakout only
    inside = _bar("XAUUSD", _ts(2026, 10, 6, 7, 45), 2008.0, 2005.0, 2006.0)
    assert OpeningRangeBreakoutStrategy().detect_setup(bars + [inside]) is None


def test_asian_range_breakout_is_gold_only() -> None:
    day = _ts(2026, 10, 6)
    bars = [_bar("XAUUSD", day + 300 * i, 1998.0 + (i % 3), 1992.0 + (i % 3), 1995.0) for i in range(72)]
    breakout = _bar("XAUUSD", day + 6 * 3600 + 600, 2002.0, 1996.0, 2001.5)
    setup = AsianRangeBreakoutStrategy().detect_setup(bars + [breakout])
    assert setup["strategy"] == "ASIAN_RANGE_BREAKOUT"
    assert (setup["trade_direction"], setup["entry_price"]) == ("LONG", 2000.0)
    euro = [Candle("EURUSD", c.timeframe, c.timestamp, c.open, c.high, c.low, c.close, 0.0) for c in bars + [breakout]]
    assert AsianRangeBreakoutStrategy().detect_setup(euro) is None
    late = _bar("XAUUSD", day + 11 * 3600, 2002.0, 1996.0, 2001.5)
    assert AsianRangeBreakoutStrategy().detect_setup(bars + [late]) is None


def test_one_way_day_blocks_trades_against_it() -> None:
    yesterday = _ts(2026, 10, 5)
    bars = [_bar("XAUUSD", yesterday + 300 * i, 2005.0, 1995.0, 2000.0) for i in range(288)]
    today = yesterday + 86400
    bars += [_bar("XAUUSD", today + 300 * i, 2000.5 + i * 0.5, 1999.5 + i * 0.5, 2000.3 + i * 0.5,
                  open_=2000.0 + i * 0.5) for i in range(48)]
    assert trend_day_direction(bars) == "UP"
    assert "no sells" in against_trend_day(bars, "SHORT")
    assert against_trend_day(bars, "LONG") is None
    assert trend_day_direction(bars[:300]) is None  # too early in the day


def test_fx_prices_keep_their_decimals() -> None:
    mother = _bar("GBPUSD", 1000, 1.32050, 1.31900, 1.32000)
    inside = _bar("GBPUSD", 1300, 1.32030, 1.31950, 1.32010)
    trap = _bar("GBPUSD", 1600, 1.32040, 1.31870, 1.31987)
    setup = InsideBarTrapStrategy().detect_setup([mother, inside, trap])
    assert setup["entry_price"] == 1.3204 and setup["sl_price"] == 1.3187


# --- Trial routing and budgets ------------------------------------------------------------

def test_trial_signals_skip_public_budgets_and_public_caps_ignore_trials() -> None:
    governor = RiskGovernor(max_concurrent_signals=1)
    public_open = Signal("XAUUSD", "LONG", 2400, 2395, 2407.5, 2415, 80, "x", 1, "p1")
    repository = MagicMock()
    repository.get_kv.return_value = None
    repository.get_open_signals.return_value = [public_open]
    repository.count_signals_since.return_value = 0
    repository.get_closed_results_since.return_value = []
    now = _ts(2026, 10, 7, 12)
    assert not governor.is_trading_allowed(repository, now, "EURUSD", "SHORT")[0]
    assert governor.is_trading_allowed(repository, now, "US100", "LONG", trial=True)[0]
    trial_open = Signal("US100", "LONG", 25000, 24970, 25045, 25090, 80, "x", 1, "t1", trial=True)
    repository.get_open_signals.return_value = [trial_open]
    assert governor.is_trading_allowed(repository, now, "EURUSD", "SHORT")[0]


def test_trial_results_do_not_touch_public_cooldowns(tmp_path: Path) -> None:
    from src.alerting.lifecycle_manager import SignalLifecycleManager

    repository = _repo(tmp_path)
    trial = Signal("US100", "LONG", 25000, 24970, 25045, 25090, 80, "x", _ts(2026, 10, 7, 14), "t1", trial=True)
    SignalLifecycleManager._record_risk_outcome(repository, "SL_HIT", _bar("US100", _ts(2026, 10, 7, 15), 1, 1, 1), trial)
    assert repository.get_kv("risk_consecutive_sl_count") is None
    repository.close()


def test_trial_signal_goes_to_the_owner_only(tmp_path: Path, monkeypatch) -> None:
    from src.core.orchestrator import PulseOrchestrator

    repository = _repo(tmp_path)
    telegram = MagicMock()
    telegram.chat_id = "public"
    chats = []
    telegram.send_message.side_effect = lambda *a, **k: chats.append(telegram.chat_id) or 10 + len(chats)
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    candles = [_bar("US100", _ts(2026, 10, 7, 14, 0) + 300 * i, 25010.0, 24990.0, 25000.0) for i in range(20)]
    setup = {"trade_direction": "LONG", "strategy": "PIN_BAR_REJECTION", "entry_price": 25000.0, "sl_price": 24960.0}

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "owner")
    saved, errors = orchestrator._persist_actionable_signal(repository, setup, candles, candles[-1], 80)
    assert saved and errors == 0 and chats and set(chats) == {"owner"}
    assert repository.query("SELECT trial FROM signals;")[0][0] == 1

    chats.clear()
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "")
    setup2 = dict(setup, entry_price=25100.0, sl_price=25060.0)
    saved, _ = orchestrator._persist_actionable_signal(repository, setup2, candles, candles[-1], 80)
    assert saved and chats == []  # no owner chat: tracked quietly, never public
    repository.close()


def test_owner_sees_how_each_trial_is_doing(tmp_path: Path) -> None:
    from src.alerting.owner_reports import trial_scorecard

    repository = _repo(tmp_path)
    for index in range(31):
        signal = Signal("US100", "LONG", 25000, 24970, 25045, 25090, 80, "x", 1000 + index, f"t{index}",
                        strategy="PIN_BAR_REJECTION", trial=True)
        repository.save_signal(signal)
        win = index % 2 == 0
        repository.update_signal_closure(f"t{index}", "x", "CLOSED_TP2" if win else "CLOSED_SL",
                                         realized_r=2.0 if win else -1.0, closed_at=2000 + index)
    lines = trial_scorecard(repository)
    assert len(lines) == 1 and "US100 (Nasdaq)" in lines[0] and "31 trades" in lines[0]
    assert "ready to go public" in lines[0]
    repository.close()


@pytest.mark.parametrize("symbol", ["US100", "US500", "USDJPY", "AUDUSD", "ETHUSD", "XAGUSD", "WTIUSD"])
def test_every_new_market_has_costs_and_a_history_source(symbol: str) -> None:
    instrument = INSTRUMENTS[symbol]
    assert instrument.typical_spread > 0 and instrument.slippage > 0 and instrument.trial
    assert instrument.history_source in {"HISTDATA", "BINANCE"} and instrument.code_prefix
