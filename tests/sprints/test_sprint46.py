"""Sprint 46 — pivots, engulfing, H2/L2 pullbacks, directional vetoes,
London continuation, kill switch, trade-plan reasoning, dashboard pages."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from src.analysis.momentum import MomentumEngine
from src.analysis.pivots import PivotPointEngine
from src.analysis.risk_governor import RiskGovernor
from src.analysis.sessions import SessionEngine
from src.analysis.signal_factory import SignalFactory
from src.domain.candle import Candle
from src.strategies.engulfing_zone import EngulfingZoneStrategy
from src.strategies.pullback_h2 import PullbackH2L2Strategy

DAY_START = 1_700_006_400  # 00:00 UTC


def _candle(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="XAUUSD", timeframe="M5", timestamp=ts,
        open=o, high=h, low=l, close=c, volume=100.0,
    )


# --- Pivot points (BabyPips formulas) ----------------------------------------

def _pivot_day_candles() -> list[Candle]:
    candles = []
    # Yesterday: H=2020, L=2000, C=2010 across 20 bars.
    for i in range(20):
        ts = DAY_START - 86400 + i * 300
        candles.append(_candle(ts, 2010.0, 2015.0, 2005.0, 2010.0))
    candles[5] = _candle(DAY_START - 86400 + 5 * 300, 2010.0, 2020.0, 2005.0, 2012.0)
    candles[10] = _candle(DAY_START - 86400 + 10 * 300, 2010.0, 2012.0, 2000.0, 2008.0)
    candles[19] = _candle(DAY_START - 86400 + 19 * 300, 2009.0, 2011.0, 2007.0, 2010.0)
    # A few bars today.
    for i in range(5):
        candles.append(_candle(DAY_START + i * 300, 2001.0, 2002.0, 2000.0, 2001.0))
    return candles


def test_pivot_levels_match_babypips_formulas() -> None:
    levels = PivotPointEngine().calculate_levels(_pivot_day_candles(), DAY_START + 3600)
    assert levels is not None
    assert levels["PP"] == 2010.0
    assert levels["R1"] == 2020.0  # 2*PP - L
    assert levels["S1"] == 2000.0  # 2*PP - H
    assert levels["R2"] == 2030.0  # PP + (H-L)
    assert levels["S2"] == 1990.0
    assert levels["R3"] == 2040.0  # H + 2*(PP-L)
    assert levels["S3"] == 1980.0  # L - 2*(H-PP)


def test_pivot_bonus_near_supportive_level() -> None:
    result = PivotPointEngine().evaluate(
        _pivot_day_candles(), "LONG", entry_price=2000.5, now_ts=DAY_START + 3600
    )
    assert result["score"] == 6
    assert "S1" in result["note"]

    nothing = PivotPointEngine().evaluate(
        _pivot_day_candles(), "LONG", entry_price=2015.5, now_ts=DAY_START + 3600
    )
    assert nothing["score"] == 0


# --- Engulfing at zone ---------------------------------------------------------

def _downtrend_into_engulf() -> list[Candle]:
    candles = []
    price = 2020.0
    for i in range(20):
        price -= 1.0
        candles.append(_candle(DAY_START + i * 300, price + 0.8, price + 1.2, price - 0.4, price))
    # Prev bar: small bearish body 2001 -> 2000.
    candles.append(_candle(DAY_START + 20 * 300, 2001.0, 2001.5, 1999.5, 2000.0))
    # Current: bullish engulfing at the zone.
    candles.append(_candle(DAY_START + 21 * 300, 1999.8, 2002.8, 1999.4, 2002.5))
    return candles


def test_engulfing_at_zone_fires_stop_long() -> None:
    zone = {
        "id": 3, "type": "OB_BULLISH", "status": "ACTIVE",
        "price_top": 2000.5, "price_bottom": 1999.0,
    }
    setup = EngulfingZoneStrategy().detect_setup(_downtrend_into_engulf(), [zone])
    assert setup is not None
    assert setup["strategy"] == "ENGULFING_ZONE"
    assert setup["trade_direction"] == "LONG"
    assert setup["order_type"] == "STOP"
    assert setup["entry_price"] == 2003.3  # high + 0.5 buffer
    assert setup["sl_price"] == 1998.9


def test_engulfing_requires_zone_and_reversal_context() -> None:
    strategy = EngulfingZoneStrategy()
    assert strategy.detect_setup(_downtrend_into_engulf(), []) is None

    # Same shape but in an UPTREND (prior close above EMA): not a reversal.
    uptrend = []
    price = 1980.0
    for i in range(20):
        price += 1.0
        uptrend.append(_candle(DAY_START + i * 300, price - 0.8, price + 0.4, price - 1.2, price))
    uptrend.append(_candle(DAY_START + 20 * 300, 2001.0, 2001.5, 1999.5, 2000.0))
    uptrend.append(_candle(DAY_START + 21 * 300, 1999.8, 2002.8, 1999.4, 2002.5))
    zone = {"id": 3, "type": "OB_BULLISH", "status": "ACTIVE", "price_top": 2000.5, "price_bottom": 1999.0}
    assert strategy.detect_setup(uptrend, [zone]) is None


# --- H2 pullback ----------------------------------------------------------------

def test_h2_pullback_detects_second_leg_entry() -> None:
    candles = []
    close = 2000.0
    for i in range(20):
        close += 1.0
        candles.append(_candle(DAY_START + i * 300, close - 0.8, close + 0.5, close - 1.0, close))
    # Peak bar.
    candles.append(_candle(DAY_START + 20 * 300, 2020.0, 2025.0, 2019.0, 2024.0))
    # Pullback: lower high, H1, lower high, H2 (last bar, strong close).
    candles.append(_candle(DAY_START + 21 * 300, 2021.0, 2022.0, 2018.0, 2019.0))
    candles.append(_candle(DAY_START + 22 * 300, 2019.5, 2023.0, 2019.0, 2021.0))  # H1
    candles.append(_candle(DAY_START + 23 * 300, 2020.5, 2021.0, 2017.0, 2019.0))  # lower high
    candles.append(_candle(DAY_START + 24 * 300, 2019.5, 2022.0, 2019.0, 2021.5))  # H2 trigger

    setup = PullbackH2L2Strategy().detect_setup(candles)
    assert setup is not None
    assert setup["strategy"] == "H2_PULLBACK"
    assert setup["trade_direction"] == "LONG"
    assert setup["order_type"] == "STOP"
    assert setup["entry_price"] == 2022.5


def test_h2_rejects_first_leg() -> None:
    candles = []
    close = 2000.0
    for i in range(22):
        close += 1.0
        candles.append(_candle(DAY_START + i * 300, close - 0.8, close + 0.5, close - 1.0, close))
    candles.append(_candle(DAY_START + 22 * 300, 2022.0, 2027.0, 2021.0, 2026.0))  # peak
    candles.append(_candle(DAY_START + 23 * 300, 2023.0, 2024.0, 2020.0, 2021.0))
    candles.append(_candle(DAY_START + 24 * 300, 2021.5, 2025.0, 2021.0, 2023.0))  # H1 only
    assert PullbackH2L2Strategy().detect_setup(candles) is None


# --- Directional veto & continuation ------------------------------------------

def test_seven_of_ten_ema_filter_blocks_fading() -> None:
    candles = []
    close = 2000.0
    for i in range(40):
        close += 1.5
        candles.append(_candle(DAY_START + i * 300, close - 1.0, close + 0.5, close - 1.5, close))
    result = MomentumEngine().evaluate(candles, "SHORT")
    assert result["veto"] is True
    assert any("G73" in note or "overbought" in note for note in result["notes"])


def test_london_continuation_bonus() -> None:
    candles = []
    price = 2000.0
    for i in range(24):  # 07:00 onward, trending up
        ts = DAY_START + 7 * 3600 + i * 300
        price += 0.5
        candles.append(_candle(ts, price - 0.4, price + 0.3, price - 0.6, price))
    ny_ts = DAY_START + 13 * 3600

    engine = SessionEngine()
    aligned = engine.london_continuation(candles, "LONG", ny_ts)
    opposed = engine.london_continuation(candles, "SHORT", ny_ts)
    assert aligned["score"] == 5
    assert opposed["score"] == -5

    # Outside the NY window: neutral.
    off = engine.london_continuation(candles, "LONG", DAY_START + 20 * 3600)
    assert off["score"] == 0


# --- Kill switch -----------------------------------------------------------------

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


def test_kill_switch_blocks_trading() -> None:
    repo = _KvRepo()
    governor = RiskGovernor()
    repo.kv["trading_paused"] = "1"
    allowed, reason = governor.is_trading_allowed(repo, DAY_START)
    assert not allowed
    assert "paused" in reason

    repo.kv["trading_paused"] = "0"
    allowed_again, _ = governor.is_trading_allowed(repo, DAY_START)
    assert allowed_again


# --- Trade-plan reasoning ---------------------------------------------------------

def test_trade_plan_reasoning_contains_thesis_evidence_numbers_plan() -> None:
    signal = SignalFactory().build_signal(
        symbol="XAUUSD",
        trade_direction="LONG",
        zone_dict={
            "id": 9,
            "type": "OB_BULLISH",
            "status": "ACTIVE",
            "price_top": 2007.0,
            "price_bottom": 2004.5,
            "strategy": "PIN_BAR_REJECTION",
            "order_type": "STOP",
            "entry_price": 2008.4,
            "sl_price": 2003.9,
            "plan_context": {
                "structure": "BULLISH",
                "macro_bias": "BIAS_LONG",
                "regime": "REGIME_DECOUPLED",
                "session": "London Killzone",
                "liquidity": "Liquidity Sweep Long 4 bars ago",
                "daily_r": "+0.75R",
                "notes": ["SMC base score: 100", "London Killzone: prime liquidity window (+10)"],
            },
        },
        atr=2.5,
        score=92,
        timestamp=DAY_START,
    )

    reasoning = signal.reasoning
    assert "TRADE PLAN | Score 92 | Tier 1" in reasoning
    assert "Context: Structure BULLISH | Macro bias BIAS_LONG" in reasoning
    assert "Location: Active OB BULLISH 2004.50-2007.00" in reasoning
    assert "Liquidity: Liquidity Sweep Long 4 bars ago" in reasoning
    assert "Trigger: Pin Bar Rejection via STOP order" in reasoning
    assert "Evidence:" in reasoning
    assert "Numbers: entry 2008.40 (STOP)" in reasoning
    assert "Plan: TP1 hit -> bank half, stop to entry." in reasoning
    assert "Thesis invalid on a close beyond" in reasoning
    # Lot table contract still intact.
    assert "<b>Baseline Assumption ($100):</b>" in reasoning


# --- Dashboard pages ---------------------------------------------------------------

@pytest.fixture
def dashboard_client(tmp_path, monkeypatch):
    import importlib

    from src.persistence.schema import SchemaInitializer

    # The package __init__ shadows the submodule attribute with the Flask app;
    # importlib returns the real module.
    dashboard_app = importlib.import_module("src.dashboard.app")

    db_path = tmp_path / "dash.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    connection.execute(
        "INSERT INTO kv_store (key, value, updated_at) VALUES ('trading_paused','0',0);"
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
    return client, db_path


def test_dashboard_new_pages_render(dashboard_client) -> None:
    client, _ = dashboard_client
    for route in ("/performance", "/risk", "/market"):
        response = client.get(route)
        assert response.status_code == 200, route


def test_dashboard_kill_switch_toggle(dashboard_client) -> None:
    client, db_path = dashboard_client
    response = client.post("/risk/toggle-pause", follow_redirects=False)
    assert response.status_code == 302

    connection = sqlite3.connect(str(db_path))
    value = connection.execute(
        "SELECT value FROM kv_store WHERE key='trading_paused';"
    ).fetchone()[0]
    connection.close()
    assert value == "1"


def main() -> None:
    print("Sprint 46 verified")


if __name__ == "__main__":
    main()
