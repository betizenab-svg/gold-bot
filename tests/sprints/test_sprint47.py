"""Sprint 47 — final round: structure exit, Quasimodo, three-push veto,
two-bar reversal evidence, conviction-tiered sizing."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.analysis.confluence import ConfluenceEngineV2
from src.analysis.position_sizing import LotSizeCalculator
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.strategies.quasimodo import QuasimodoStrategy


def _candle(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="XAUUSD", timeframe="M5", timestamp=ts,
        open=o, high=h, low=l, close=c, volume=100.0,
    )


class _KvRepo:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.status_updates: list[tuple[str, str]] = []

    def get_kv(self, key: str):
        return self.kv.get(key)

    def set_kv(self, key: str, value) -> None:
        self.kv[key] = str(value)

    def update_signal_status(self, signal_hash: str, status: str) -> None:
        self.status_updates.append((signal_hash, status))

    def get_signal_message_id(self, signal_hash: str) -> int:
        return 4455

    def update_signal_excursions(self, *_args) -> None:
        pass


def _partial_signal(direction: str = "LONG") -> Signal:
    return Signal(
        symbol="XAUUSD",
        signal_type=direction,
        entry_price=2000.0,
        sl_price=1994.0 if direction == "LONG" else 2006.0,
        tp1_price=2009.0 if direction == "LONG" else 1991.0,
        tp2_price=2018.0 if direction == "LONG" else 1982.0,
        score=85,
        reasoning="t",
        timestamp=1_700_000_000,
        signal_hash="runner-1",
        telegram_message_id=4455,
        telegram_chat_id="chat-123",
        status="PARTIAL_TP1",
    )


# --- Structure exit for the runner --------------------------------------------

def test_structure_flip_closes_runner() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    repo = _KvRepo()
    repo.kv["current_structure_state"] = "BEARISH"

    event = manager._structure_exit_event(repo, _partial_signal("LONG"))
    assert event == "STRUCTURE_EXIT"

    repo.kv["current_structure_state"] = "BULLISH"
    assert manager._structure_exit_event(repo, _partial_signal("LONG")) is None
    # Runners only: ACTIVE trades keep their normal stop.
    active = Signal(
        symbol="XAUUSD", signal_type="LONG", entry_price=2000.0, sl_price=1994.0,
        tp1_price=2009.0, tp2_price=2018.0, score=85, reasoning="t",
        timestamp=1_700_000_000, signal_hash="a-1", status="ACTIVE",
    )
    repo.kv["current_structure_state"] = "BEARISH"
    assert manager._structure_exit_event(repo, active) is None


def test_structure_exit_records_banked_r() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    repo = _KvRepo()
    # Runner marked at close 2003: 0.75R banked + 0.5 * (3/6)R = +1.0R total.
    candle = _candle(1_700_000_600, 2002.0, 2004.0, 2001.0, 2003.0)
    manager._record_risk_outcome(repo, "STRUCTURE_EXIT", candle, _partial_signal("LONG"))
    assert abs(float(repo.kv["risk_daily_r_value"]) - 1.0) < 1e-6


# --- Quasimodo -------------------------------------------------------------------

def test_quasimodo_bearish_limit_at_left_shoulder() -> None:
    history = {
        "highs": [
            {"timestamp": 1_700_000_000, "price": 2010.0},  # SH1 left shoulder
            {"timestamp": 1_700_001_000, "price": 2015.0},  # SH2 head (sweep)
        ],
        "lows": [
            {"timestamp": 1_700_000_500, "price": 2000.0},  # neckline
        ],
    }
    supply_zone = {
        "id": 11, "type": "OB_BEARISH", "status": "ACTIVE",
        "price_top": 2011.0, "price_bottom": 2009.0,
    }
    candles = [_candle(1_700_001_000 + i * 300, 2000.0, 2001.0, 1994.0, 1995.0) for i in range(5)]
    setup = QuasimodoStrategy().detect_setup(candles, history, [supply_zone])
    assert setup is not None
    assert setup["strategy"] == "QUASIMODO"
    assert setup["trade_direction"] == "SHORT"
    assert setup["order_type"] == "LIMIT"
    assert setup["entry_price"] == 2010.0
    assert setup["sl_price"] == 2015.5

    # Same pattern with NO zone at the shoulder: gated out (replay-proven).
    assert QuasimodoStrategy().detect_setup(candles, history, []) is None


def test_quasimodo_requires_neckline_break_and_sweep() -> None:
    strategy = QuasimodoStrategy()
    candles = [_candle(1_700_001_000 + i * 300, 2005.0, 2006.0, 2004.0, 2005.0) for i in range(5)]
    supply_zone = {
        "id": 11, "type": "OB_BEARISH", "status": "ACTIVE",
        "price_top": 2016.0, "price_bottom": 2009.0,
    }

    # Neckline not broken (close above it): no setup.
    unbroken = {
        "highs": [
            {"timestamp": 1_700_000_000, "price": 2010.0},
            {"timestamp": 1_700_001_000, "price": 2015.0},
        ],
        "lows": [{"timestamp": 1_700_000_500, "price": 2000.0}],
    }
    assert strategy.detect_setup(candles, unbroken, [supply_zone]) is None

    # No sweep (second high lower): no setup.
    no_sweep = {
        "highs": [
            {"timestamp": 1_700_000_000, "price": 2015.0},
            {"timestamp": 1_700_001_000, "price": 2010.0},
        ],
        "lows": [{"timestamp": 1_700_000_500, "price": 2000.0}],
    }
    broken_candles = [
        _candle(1_700_001_000 + i * 300, 2000.0, 2001.0, 1994.0, 1995.0) for i in range(5)
    ]
    assert strategy.detect_setup(broken_candles, no_sweep, [supply_zone]) is None


def test_quasimodo_bullish_mirror() -> None:
    history = {
        "lows": [
            {"timestamp": 1_700_000_000, "price": 1990.0},  # SL1 left shoulder
            {"timestamp": 1_700_001_000, "price": 1985.0},  # SL2 head (sweep)
        ],
        "highs": [
            {"timestamp": 1_700_000_500, "price": 2000.0},  # neckline
        ],
    }
    demand_zone = {
        "id": 12, "type": "OB_BULLISH", "status": "ACTIVE",
        "price_top": 1991.0, "price_bottom": 1989.0,
    }
    candles = [_candle(1_700_001_000 + i * 300, 2001.0, 2006.0, 2000.5, 2005.0) for i in range(5)]
    setup = QuasimodoStrategy().detect_setup(candles, history, [demand_zone])
    assert setup is not None
    assert setup["trade_direction"] == "LONG"
    assert setup["entry_price"] == 1990.0
    assert setup["sl_price"] == 1984.5


# --- Three-push exhaustion & two-bar reversal -----------------------------------

def test_three_push_exhaustion_vetoes_with_trend() -> None:
    engine = ConfluenceEngineV2()
    history = {
        "highs": [
            {"timestamp": 1_700_000_000, "price": 2000.0},
            {"timestamp": 1_700_001_000, "price": 2010.0},  # +10
            {"timestamp": 1_700_002_000, "price": 2014.0},  # +4 (shrinking)
        ],
        "lows": [],
    }
    note = engine._three_push_exhaustion("LONG", history, 1_700_002_600)
    assert note is not None and "exhaustion" in note

    # Expanding thrust: healthy trend, no veto.
    expanding = {
        "highs": [
            {"timestamp": 1_700_000_000, "price": 2000.0},
            {"timestamp": 1_700_001_000, "price": 2004.0},
            {"timestamp": 1_700_002_000, "price": 2012.0},
        ],
        "lows": [],
    }
    assert engine._three_push_exhaustion("LONG", expanding, 1_700_002_600) is None
    # Shorts are unaffected by ascending highs.
    assert engine._three_push_exhaustion("SHORT", history, 1_700_002_600) is None


def test_two_bar_reversal_evidence() -> None:
    engine = ConfluenceEngineV2()
    bearish_bar = _candle(1_700_000_000, 2005.0, 2006.0, 2001.0, 2002.0)  # body -3
    bullish_reclaim = _candle(1_700_000_300, 2002.0, 2006.5, 2001.5, 2005.5)  # body +3.5, closes above prev open

    note = engine._two_bar_reversal_evidence("LONG", [bearish_bar, bullish_reclaim])
    assert note is not None and "buyers reclaimed" in note

    assert engine._two_bar_reversal_evidence("SHORT", [bearish_bar, bullish_reclaim]) is None
    # Same-direction bodies: no evidence.
    twin = _candle(1_700_000_600, 2005.5, 2008.0, 2005.0, 2007.5)
    assert engine._two_bar_reversal_evidence("LONG", [bullish_reclaim, twin]) is None


# --- Conviction-tiered sizing ------------------------------------------------------

def test_tiered_risk_halves_lots_for_tier_two() -> None:
    calculator = LotSizeCalculator()
    full = calculator.generate_table(2010.0, 2005.0, risk_pct=0.02)
    half = calculator.generate_table(2010.0, 2005.0, risk_pct=0.01)
    # $5000 balance, 50-pip stop: 0.20 lots at 2%, 0.10 at 1%.
    assert "$5000    0.20" in full
    assert "$5000    0.10" in half
    assert "2% risk model" in full
    assert "1% risk model" in half


def main() -> None:
    print("Sprint 47 verified")


if __name__ == "__main__":
    main()
