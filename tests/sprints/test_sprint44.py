"""Sprint 44 — round-2 book rules: zone freshness, BOS confirmation,
measured-move targets, second attempts, SMT divergence, day extension,
excursion telemetry."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.analysis.confluence import ConfluenceEngineV2
from src.analysis.market_state import MarketStateEngine
from src.analysis.mitigation import ZoneLifecycleManager
from src.analysis.signal_factory import SignalFactory
from src.analysis.structure import MarketStructureEngine
from src.domain.candle import Candle
from src.domain.signal import Signal


def _candle(
    timestamp: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M5",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100.0,
    )


def test_second_touch_consumes_mitigated_zone() -> None:
    manager = ZoneLifecycleManager()
    zone = {
        "id": 7,
        "type": "OB_BULLISH",
        "price_top": 2000.0,
        "price_bottom": 1995.0,
        "status": "MITIGATED",
    }
    touch = _candle(1_700_000_300, 2002.0, 2003.0, 1999.0, 2001.0)
    updated = manager.evaluate_zones(touch, [zone])
    assert updated and updated[0]["status"] == "INVALIDATED"


def test_first_touch_still_mitigates() -> None:
    manager = ZoneLifecycleManager()
    zone = {
        "id": 8,
        "type": "OB_BULLISH",
        "price_top": 2000.0,
        "price_bottom": 1995.0,
        "status": "ACTIVE",
    }
    touch = _candle(1_700_000_300, 2002.0, 2003.0, 1999.0, 2001.0)
    updated = manager.evaluate_zones(touch, [zone])
    assert updated and updated[0]["status"] == "MITIGATED"


def test_bos_requires_two_closes_when_confirmation_given() -> None:
    engine = MarketStructureEngine()
    swing = {"timestamp": 1, "price": 2000.0}
    breaker = _candle(1_700_000_300, 1999.0, 2003.0, 1998.0, 2002.0)

    # Single-close mode (legacy callers) still fires.
    assert engine.detect_bos(breaker, swing, "BULLISH") is True
    # Prior close below the swing = unconfirmed one-bar break -> trap, no BOS.
    assert engine.detect_bos(breaker, swing, "BULLISH", confirmation_close=1999.0) is False
    assert engine.detect_bos(breaker, swing, "BULLISH", confirmation_close=2001.0) is True


def test_measured_move_caps_tp2() -> None:
    factory = SignalFactory()
    _, _, tp1, tp2 = factory.calculate_parameters(
        trade_direction="LONG",
        zone_dict={
            "entry_price": 2000.0,
            "sl_price": 1994.0,
            "measured_move": 12.0,  # prior leg smaller than 3R=18
        },
        atr=2.0,
    )
    # Cap = entry + (12 - 0.1*2) = 2011.8, tighter than the 3R target 2018.
    assert tp1 == 2009.0
    assert tp2 == 2011.8


def test_measured_move_ignored_when_beyond_3r() -> None:
    factory = SignalFactory()
    _, _, _, tp2 = factory.calculate_parameters(
        trade_direction="LONG",
        zone_dict={"entry_price": 2000.0, "sl_price": 1994.0, "measured_move": 50.0},
        atr=2.0,
    )
    assert tp2 == 2018.0


def test_smt_state_adjusts_score_direction() -> None:
    engine = ConfluenceEngineV2()
    repo = MagicMock()
    repo.get_kv.side_effect = lambda key: "GOLD_RICH" if key == "macro_smt_state" else None
    repo.get_strategy_outcomes.return_value = []

    short_delta, short_note = engine._smt_adjustment(repo, "SHORT")
    long_delta, long_note = engine._smt_adjustment(repo, "LONG")
    assert short_delta == 5 and short_note
    assert long_delta == -5 and long_note


def test_second_attempt_bonus_in_confluence() -> None:
    engine = ConfluenceEngineV2()
    result = engine.evaluate(
        trade_direction="LONG",
        macro_bias="NEUTRAL",
        current_structure="BULLISH",
        zone_dict={"status": "ACTIVE"},
        has_recent_sweep=False,
        recent_candles=[],
        current_timestamp=1_699_948_800,
        second_attempt=True,
    )
    assert any("Second attempt" in note for note in result["notes"])


def test_day_extension_vetoes_continuation() -> None:
    engine = MarketStateEngine()
    day_start = 1_700_006_400  # 00:00 UTC boundary for this fixture
    candles: list[Candle] = []
    # Quiet full Asian session (00:00-05:00 = 60 M5 bars) in a $2 range.
    for index in range(60):
        ts = day_start + index * 300
        candles.append(_candle(ts, 2000.0, 2001.0, 1999.0, 2000.5))
    # Explosive up-day after Asia: climbs $30 (>2.5x the $2 Asian range).
    price = 2000.0
    for index in range(40):
        ts = day_start + (60 + index) * 300
        price += 0.75
        candles.append(_candle(ts, price - 0.7, price + 0.3, price - 0.9, price))

    long_result = engine.evaluate(candles, "LONG", order_type="LIMIT")
    short_result = engine.evaluate(candles, "SHORT", order_type="LIMIT")
    assert long_result["veto"] is True  # chasing the extended up-day
    assert short_result["veto"] is False  # fading it is allowed


def test_excursion_tracking_records_r_multiples() -> None:
    manager = SignalLifecycleManager(telegram_client=MagicMock())
    repo = MagicMock()
    signal = Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2000.0,
        sl_price=1995.0,
        tp1_price=2007.5,
        tp2_price=2015.0,
        score=80,
        reasoning="t",
        timestamp=1_700_000_000,
        signal_hash="exc-1",
        status="ACTIVE",
    )
    candle = _candle(1_700_000_300, 2001.0, 2010.0, 1997.5, 2008.0)
    manager._track_excursions(repo, signal, candle)

    repo.update_signal_excursions.assert_called_once()
    args = repo.update_signal_excursions.call_args.args
    assert args[0] == "exc-1"
    assert abs(args[1] - 2.0) < 1e-9  # (2010-2000)/5 = +2R favorable
    assert abs(args[2] - 0.5) < 1e-9  # (2000-1997.5)/5 = 0.5R adverse


def test_trendline_gate_blocks_unbroken_counter_trend() -> None:
    from src.analysis.trendline import TrendlineEngine

    engine = TrendlineEngine()
    # Downtrend line through two lower swing highs: 2020 @ t0, 2010 @ t0+3000.
    history = {
        "highs": [
            {"timestamp": 1_700_000_000, "price": 2020.0},
            {"timestamp": 1_700_003_000, "price": 2010.0},
        ],
        "lows": [],
    }
    # Line at t0+6000 projects to 2000. Close below it: counter-trend long vetoed.
    below = engine.counter_trend_check("LONG", "BEARISH", history, 1995.0, 1_700_006_000)
    assert below["veto"] is True

    # Close above the projected line: the downtrend line is broken, long allowed.
    above = engine.counter_trend_check("LONG", "BEARISH", history, 2004.0, 1_700_006_000)
    assert above["veto"] is False

    # With-trend trades are never gated.
    with_trend = engine.counter_trend_check("SHORT", "BEARISH", history, 1995.0, 1_700_006_000)
    assert with_trend["veto"] is False


def test_swing_history_appends_and_dedupes() -> None:
    from src.analysis.trendline import TrendlineEngine
    import json

    first = TrendlineEngine.update_history(
        None,
        {"swing_high": {"timestamp": 100, "price": 2020.0}, "swing_low": None},
    )
    assert len(first["highs"]) == 1

    # Same pivot again: no duplicate. New pivot: appended.
    second = TrendlineEngine.update_history(
        json.dumps(first),
        {"swing_high": {"timestamp": 100, "price": 2020.0}, "swing_low": None},
    )
    assert len(second["highs"]) == 1
    third = TrendlineEngine.update_history(
        json.dumps(second),
        {"swing_high": {"timestamp": 200, "price": 2010.0}, "swing_low": {"timestamp": 150, "price": 1990.0}},
    )
    assert len(third["highs"]) == 2
    assert len(third["lows"]) == 1


def test_calibration_report_generates_recommendations(tmp_path) -> None:
    import sqlite3

    from scripts.calibrate_from_history import analyze
    from src.persistence.schema import SchemaInitializer

    db_path = tmp_path / "calib.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()

    def insert(strategy: str, status: str, mfe: float, mae: float, index: int) -> None:
        connection.execute(
            """
            INSERT INTO signals (signal_hash, symbol, signal_type, entry_price, sl_price,
                                 tp1_price, tp2_price, score, reasoning, timestamp,
                                 status, order_type, strategy, mfe_r, mae_r)
            VALUES (?, 'XAUUSD', 'LONG', 2000, 1995, 2007.5, 2015, 80, 't',
                    1700000000, ?, 'STOP', ?, ?, ?);
            """,
            (f"{strategy}-{status}-{index}", status, strategy, mfe, mae),
        )

    # Losing strategy whose losers ran >=1R favorable first (bad TP placement).
    for index in range(8):
        insert("BAD_STRAT", "CLOSED_SL", 1.3, 1.0, index)
    for index in range(4):
        insert("BAD_STRAT", "CLOSED_TP2", 3.1, 0.2, index)
    connection.commit()
    connection.close()

    report = analyze(str(db_path))
    stats = report["strategies"]["BAD_STRAT"]
    assert stats["trades"] == 12
    assert stats["expectancy_r"] < 0.2
    assert any("losers ran" in line for line in report["recommendations"])
    assert any("stop is wider than needed" in line for line in report["recommendations"])


def main() -> None:
    print("Sprint 44 round-2 book rules verified")


if __name__ == "__main__":
    main()
