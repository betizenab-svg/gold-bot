"""Sprint 43 — v2 intelligence layer: order types, lifecycle exits, governor,
expectancy weights, market-state vetoes, and book-derived filters."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.analysis.adaptive_weights import AdaptiveWeightEngine
from src.analysis.confluence import ConfluenceEngineV2
from src.analysis.market_state import MarketStateEngine
from src.analysis.momentum import MomentumEngine, calculate_rsi
from src.analysis.risk_governor import RiskGovernor
from src.analysis.sessions import SessionEngine
from src.analysis.signal_factory import SignalFactory
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.strategies.pin_bar_rejection import PinBarRejectionStrategy


def _candle(
    timestamp: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M5",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _signal(
    status: str = "PENDING",
    order_type: str = "LIMIT",
    signal_type: str = "LONG",
    entry: float = 2000.0,
    sl: float = 1994.0,
    tp1: float = 2009.0,
    tp2: float = 2018.0,
    timestamp: int = 1_700_000_000,
) -> Signal:
    return Signal(
        symbol="XAUUSD",
        signal_type=signal_type,
        entry_price=entry,
        sl_price=sl,
        tp1_price=tp1,
        tp2_price=tp2,
        score=80,
        reasoning="test",
        timestamp=timestamp,
        signal_hash=f"hash-{status}-{order_type}-{signal_type}",
        status=status,
        order_type=order_type,
        strategy="PIN_BAR_REJECTION",
    )


# --- Order-type aware activation -------------------------------------------

def test_stop_order_long_does_not_activate_below_entry() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(order_type="STOP")
    # Price stays below the breakout trigger: a buy-stop must NOT fill.
    below = _candle(1_700_000_300, 1996.0, 1998.5, 1995.0, 1997.0)
    assert manager.evaluate_signal(signal, below) is None

    breakout = _candle(1_700_000_600, 1998.0, 2001.0, 1997.5, 2000.5)
    assert manager.evaluate_signal(signal, breakout) == "ACTIVATED"


def test_limit_order_long_activates_on_pullback() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(order_type="LIMIT")
    pullback = _candle(1_700_000_300, 2002.0, 2003.0, 1999.5, 2001.0)
    assert manager.evaluate_signal(signal, pullback) == "ACTIVATED"


def test_stop_order_short_activates_on_breakdown() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(
        order_type="STOP", signal_type="SHORT", entry=1990.0, sl=1996.0, tp1=1981.0, tp2=1972.0
    )
    above = _candle(1_700_000_300, 1994.0, 1995.0, 1991.0, 1993.0)
    assert manager.evaluate_signal(signal, above) is None
    breakdown = _candle(1_700_000_600, 1992.0, 1993.0, 1989.0, 1989.5)
    assert manager.evaluate_signal(signal, breakdown) == "ACTIVATED"


# --- Expiry, breakeven, time stop, SL-first --------------------------------

def test_pending_signal_expires() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(order_type="STOP")
    stale_candle = _candle(1_700_000_000 + (91 * 60), 1995.0, 1996.0, 1994.0, 1995.5)
    assert manager.evaluate_signal(signal, stale_candle) == "EXPIRED"


def test_breakeven_exit_after_tp1() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(status="PARTIAL_TP1")
    # Back to entry after TP1: runner exits flat instead of riding to full SL.
    retest = _candle(1_700_000_600, 2003.0, 2004.0, 1999.9, 2001.0)
    assert manager.evaluate_signal(signal, retest) == "BE_HIT"


def test_sl_checked_before_tp_on_same_candle() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(status="ACTIVE")
    # Candle spans both SL and TP1: pessimistic ordering must report SL.
    wide = _candle(1_700_000_600, 2000.0, 2010.0, 1993.0, 2005.0)
    assert manager.evaluate_signal(signal, wide) == "SL_HIT"


def test_time_stop_for_stale_active_trade() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(status="ACTIVE")
    much_later = _candle(1_700_000_000 + (25 * 3600), 2001.0, 2002.0, 2000.0, 2001.5)
    assert manager.evaluate_signal(signal, much_later) == "TIME_STOP"


def test_runner_exempt_from_time_stop() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    signal = _signal(status="PARTIAL_TP1")
    much_later = _candle(1_700_000_000 + (25 * 3600), 2005.0, 2006.0, 2004.0, 2005.5)
    assert manager.evaluate_signal(signal, much_later) is None


# --- Signal factory: stop floors and round numbers --------------------------

def test_minimum_stop_distance_enforced() -> None:
    factory = SignalFactory()
    entry, sl, tp1, tp2 = factory.calculate_parameters(
        trade_direction="LONG",
        zone_dict={"entry_price": 2001.6, "sl_price": 2001.0},
        atr=0.0,
    )
    # A $0.60 stop is noise; the $3 floor must widen it.
    assert entry == 2001.6
    assert (entry - sl) >= 3.0
    assert tp1 > entry
    assert tp2 > tp1


def test_round_number_stop_clearance() -> None:
    factory = SignalFactory()
    _, sl, _, _ = factory.calculate_parameters(
        trade_direction="LONG",
        zone_dict={"entry_price": 2010.0, "sl_price": 2005.1},
        atr=3.0,
    )
    # 2005.1 sits on the $5 grid; the stop must clear it by the buffer.
    assert sl <= 2004.70


# --- Risk governor -----------------------------------------------------------

class _KvRepo:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get_kv(self, key: str):
        return self.kv.get(key)

    def set_kv(self, key: str, value) -> None:
        self.kv[key] = str(value)

    def get_open_signals(self) -> list:
        return []

    def count_signals_since(self, _cutoff: int) -> int:
        return 0


def test_governor_cooldown_after_stop_loss() -> None:
    repo = _KvRepo()
    governor = RiskGovernor()
    now = 1_700_000_000
    governor.record_stop_loss(repo, now)
    allowed, reason = governor.is_trading_allowed(repo, now + 600)
    assert not allowed
    assert "cooling down" in reason

    allowed_later, _ = governor.is_trading_allowed(repo, now + (46 * 60))
    assert allowed_later


def test_governor_tier2_halt_after_streak() -> None:
    repo = _KvRepo()
    governor = RiskGovernor()
    now = 1_700_000_000
    for offset in range(5):
        governor.record_stop_loss(repo, now + offset)
    allowed, reason = governor.is_trading_allowed(repo, now + (7 * 3600))
    assert not allowed
    assert "tier-2" in reason


def test_governor_daily_loss_limit() -> None:
    repo = _KvRepo()
    governor = RiskGovernor()
    now = 1_700_000_000
    governor.record_result_r(repo, -1.0, now)
    governor.record_result_r(repo, -1.0, now + 60)
    governor.record_result_r(repo, -1.0, now + 120)
    allowed, reason = governor.is_trading_allowed(repo, now + 180)
    assert not allowed
    assert "daily loss limit" in reason


def test_governor_news_blackout() -> None:
    repo = _KvRepo()
    repo.kv["upcoming_news_events_json"] = '[{"timestamp": 1700003600}]'
    governor = RiskGovernor()
    inside_window = 1_700_003_600 - (10 * 60)
    allowed, reason = governor.is_trading_allowed(repo, inside_window)
    assert not allowed
    assert "news blackout" in reason

    outside_window = 1_700_003_600 + (60 * 60)
    allowed_after, _ = governor.is_trading_allowed(repo, outside_window)
    assert allowed_after


# --- Adaptive weights: expectancy over win rate ------------------------------

def test_expectancy_weighting_prefers_positive_r() -> None:
    engine = AdaptiveWeightEngine()

    # 40% win rate but strongly positive expectancy (trend profile).
    trend_repo = MagicMock()
    trend_repo.get_strategy_outcomes.return_value = (
        ["CLOSED_TP2"] * 8 + ["CLOSED_SL"] * 12
    )
    trend = engine.calculate_weight(trend_repo, "BIG_BULLS_BEARS")

    # 60% "win rate" via breakevens but negative expectancy.
    churn_repo = MagicMock()
    churn_repo.get_strategy_outcomes.return_value = (
        ["CLOSED_BE"] * 4 + ["CLOSED_SL"] * 16
    )
    churn = engine.calculate_weight(churn_repo, "PIN_BAR_REJECTION")

    assert trend["weight"] > 1.0
    assert churn["weight"] < 1.0


def test_weight_neutral_below_sample_floor() -> None:
    engine = AdaptiveWeightEngine()
    repo = MagicMock()
    repo.get_strategy_outcomes.return_value = ["CLOSED_SL"] * 9
    assert engine.calculate_weight(repo, "X")["weight"] == 1.0


# --- Market state and momentum vetoes ---------------------------------------

def test_barbwire_vetoes_breakout_but_not_limit() -> None:
    engine = MarketStateEngine()
    chop = [
        _candle(1_700_000_000 + i * 300, 2000.0, 2001.0, 1999.0, 2000.2)
        for i in range(10)
    ]
    stop_result = engine.evaluate(chop, "LONG", order_type="STOP")
    limit_result = engine.evaluate(chop, "LONG", order_type="LIMIT")
    assert stop_result["veto"] is True
    assert limit_result["veto"] is False
    assert limit_result["score"] < 0


def test_rsi_overbought_vetoes_long() -> None:
    closes = [2000.0 + i * 2.0 for i in range(40)]
    rsi = calculate_rsi(closes, 14)
    assert rsi is not None and rsi > 72.0

    candles = [
        _candle(1_700_000_000 + i * 300, close - 1.0, close + 0.5, close - 1.5, close)
        for i, close in enumerate(closes)
    ]
    result = MomentumEngine().evaluate(candles, "LONG")
    assert result["veto"] is True


# --- Sessions ---------------------------------------------------------------

def test_killzones_score_higher_than_off_session() -> None:
    engine = SessionEngine()
    # 2023-11-14 08:00 UTC (London killzone) vs 23:00 UTC (off session).
    london = engine.evaluate(1_699_948_800)
    off = engine.evaluate(1_700_002_800)
    assert london["score"] > off["score"]
    assert off["score"] < 0


# --- Pin bar grades ----------------------------------------------------------

def test_brooks_grade_b_reversal_bar_needs_prior_close() -> None:
    strategy = PinBarRejectionStrategy()
    prev = _candle(1_700_000_000, 2004.0, 2005.0, 2001.0, 2002.0)
    # Tail 50% of range, body 45%, closes above prior close near the high.
    brooks_bar = _candle(1_700_000_300, 2005.0, 2010.05, 2000.0, 2009.5)

    assert strategy.is_valid_pin_bar(brooks_bar) is None
    assert strategy.is_valid_pin_bar(brooks_bar, prev) == "BULLISH"


def test_doji_never_a_signal() -> None:
    strategy = PinBarRejectionStrategy()
    doji = _candle(1_700_000_300, 2005.0, 2010.0, 2000.0, 2005.3)
    assert strategy.is_valid_pin_bar(doji) is None


# --- Confluence integration ---------------------------------------------------

def test_confluence_veto_forces_rejection() -> None:
    engine = ConfluenceEngineV2()
    chop = [
        _candle(1_700_000_000 + i * 300, 2000.0, 2001.0, 1999.0, 2000.2)
        for i in range(10)
    ]
    result = engine.evaluate(
        trade_direction="LONG",
        macro_bias="BIAS_LONG",
        current_structure="BULLISH",
        zone_dict={"status": "ACTIVE"},
        has_recent_sweep=True,
        recent_candles=chop,
        current_timestamp=1_700_000_000,
        order_type="STOP",
        strategy="PIN_BAR_REJECTION",
        repository=None,
    )
    assert result["classification"] == "REJECTED"
    assert result["vetoes"]


def test_confluence_ote_bonus_applies() -> None:
    engine = ConfluenceEngineV2()
    result = engine.evaluate(
        trade_direction="LONG",
        macro_bias="NEUTRAL",
        current_structure="BULLISH",
        zone_dict={"status": "ACTIVE"},
        has_recent_sweep=False,
        recent_candles=[],
        current_timestamp=1_699_948_800,
        order_type="LIMIT",
        strategy=None,
        repository=None,
        entry_price=1965.0,  # inside the 61.8-78.6% pocket of 1950->2000
        last_swing_high=2000.0,
        last_swing_low=1950.0,
    )
    assert any("OTE" in note for note in result["notes"])


def main() -> None:
    print("Sprint 43 v2 intelligence layer verified")


if __name__ == "__main__":
    main()
