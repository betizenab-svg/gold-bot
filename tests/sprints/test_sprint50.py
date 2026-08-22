"""Sprint 50: multi-asset engine — instrument registry, per-symbol scaling,
crypto weekend handling, gold-only macro gates, kv namespacing, quarantine."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from config.instruments import get_instrument, state_key
from src.alerting.formatter import SignalFormatter
from src.alerting.lifecycle_manager import SignalLifecycleManager
from src.analysis.filters import PermissionEngine
from src.analysis.pivots import gold_session_start
from src.analysis.position_sizing import LotSizeCalculator
from src.analysis.sessions import SessionEngine
from src.analysis.signal_factory import SignalFactory
from src.core.orchestrator import PulseOrchestrator
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.validation.validator import DataValidator

# Tue 2023-11-14 08:00 UTC = 03:00 New York (EST) — London killzone.
KILLZONE_TS = 1_699_948_800
# Tue 2023-11-14 04:00 UTC = 23:00 New York — off session (Asia).
OFF_SESSION_TS = 1_699_934_400
# Sat 2023-11-18 12:00 UTC — weekend.
SATURDAY_TS = 1_700_308_800


def _candle(symbol: str, ts: int, volume: float = 100.0) -> Candle:
    return Candle(
        symbol=symbol, timeframe="M1", timestamp=ts,
        open=100.0, high=101.0, low=99.0, close=100.5, volume=volume,
    )


def test_instrument_registry_and_state_keys() -> None:
    assert get_instrument("XAUUSD").yahoo_ticker == "GC=F"
    assert get_instrument("BTCUSD").weekend_trading is True
    assert get_instrument("EURUSD").price_decimals == 5
    # Unknown symbols behave like gold (legacy default).
    assert get_instrument("UNKNOWN").symbol == "XAUUSD"
    assert get_instrument(None).symbol == "XAUUSD"
    # Gold keeps legacy kv names; other markets get suffixed keys.
    assert state_key("swing_history", "XAUUSD") == "swing_history"
    assert state_key("swing_history", "BTCUSD") == "swing_history:BTCUSD"


def test_validator_fx_volume_and_crypto_weekend() -> None:
    validator = DataValidator()
    # Yahoo FX feeds report volume=0 — must be accepted for FX only.
    assert validator.validate_candle(_candle("EURUSD", KILLZONE_TS, volume=0.0)) is True
    assert validator.validate_candle(_candle("XAUUSD", KILLZONE_TS, volume=0.0)) is False
    # Crypto trades Saturday; gold does not.
    assert validator.validate_candle(_candle("BTCUSD", SATURDAY_TS)) is True
    assert validator.validate_candle(_candle("XAUUSD", SATURDAY_TS)) is False


def test_signal_factory_eurusd_precision() -> None:
    factory = SignalFactory()
    entry, sl, tp1, tp2 = factory.calculate_parameters(
        "LONG",
        {"price_top": 1.0850, "price_bottom": 1.0842},
        atr=0.0006,
        symbol="EURUSD",
    )
    # Stop must stay in FX price territory (the old $5 grid pushed it to ~0.30).
    assert 1.05 < sl < entry
    assert entry == 1.0850
    assert round(tp1 - entry, 5) == round(1.5 * (entry - sl), 5)
    # 5-decimal precision preserved.
    assert abs(sl - round(sl, 5)) < 1e-9


def test_signal_factory_btc_minimum_stop() -> None:
    factory = SignalFactory()
    entry, sl, _tp1, _tp2 = factory.calculate_parameters(
        "LONG",
        {"price_top": 65000.0, "price_bottom": 64990.0},
        atr=20.0,
        symbol="BTCUSD",
    )
    # BTC minimum stop ($150) must override the tiny zone-based stop.
    assert entry - sl >= 150.0


def test_lot_sizing_per_instrument() -> None:
    calc = LotSizeCalculator()
    # EURUSD: 8 pips on a 0.0001 pip grid.
    assert calc.calculate_pips(1.0850, 1.0842, symbol="EURUSD") == 8.0
    # Gold legacy math unchanged: $3 stop = 30 pips.
    assert calc.calculate_pips(2400.0, 2397.0) == 30.0
    table = calc.generate_table(65000.0, 64850.0, symbol="BTCUSD")
    assert "BTCUSD" in table and "1 BTC" in table


def test_sessions_crypto_neutral_gold_penalized() -> None:
    engine = SessionEngine()
    gold_off = engine.evaluate(OFF_SESSION_TS, "XAUUSD")
    btc_off = engine.evaluate(OFF_SESSION_TS, "BTCUSD")
    assert gold_off["score"] == -10
    assert btc_off["score"] == 0
    btc_kz = engine.evaluate(KILLZONE_TS, "BTCUSD")
    assert btc_kz["score"] == 4


def test_pivot_roll_utc_for_crypto() -> None:
    assert gold_session_start(SATURDAY_TS, roll="utc0") == SATURDAY_TS - (SATURDAY_TS % 86400)
    # Gold still rolls at 17:00 New York (different anchor).
    assert gold_session_start(SATURDAY_TS, roll="ny17") != SATURDAY_TS - (SATURDAY_TS % 86400)


def test_gold_macro_gates_do_not_block_other_markets() -> None:
    engine = PermissionEngine()
    setup = {"trade_direction": "LONG"}
    macro = {"macro_cot_state": "OVERCROWDED_LONG"}
    permitted_gold, _ = engine.is_trade_permitted(setup, macro, symbol="XAUUSD")
    permitted_btc, _ = engine.is_trade_permitted(setup, macro, symbol="BTCUSD")
    assert permitted_gold is False
    assert permitted_btc is True


def test_trading_age_counts_weekend_for_crypto() -> None:
    friday_ts = 1_700_260_000  # Fri 2023-11-17 ~22:26 UTC
    sunday_ts = friday_ts + 2 * 86400
    with_weekend = SignalLifecycleManager._trading_age_seconds(
        friday_ts, sunday_ts, weekend_closed=False
    )
    without_weekend = SignalLifecycleManager._trading_age_seconds(
        friday_ts, sunday_ts, weekend_closed=True
    )
    assert with_weekend == 2 * 86400
    assert without_weekend < with_weekend


def test_structure_exit_uses_signal_symbol_namespace() -> None:
    repository = MagicMock()
    # Gold flipped BEARISH, but the BTC key says BULLISH — BTC runner must live.
    def fake_get_kv(key: str):
        return {"current_structure_state": "BEARISH",
                "current_structure_state:BTCUSD": "BULLISH"}.get(key)
    repository.get_kv.side_effect = fake_get_kv

    btc_signal = {"status": "PARTIAL_TP1", "signal_type": "LONG", "symbol": "BTCUSD"}
    gold_signal = {"status": "PARTIAL_TP1", "signal_type": "LONG", "symbol": "XAUUSD"}
    assert SignalLifecycleManager._structure_exit_event(repository, btc_signal) is None
    assert (
        SignalLifecycleManager._structure_exit_event(repository, gold_signal)
        == "STRUCTURE_EXIT"
    )


def test_formatter_uses_instrument_decimals() -> None:
    message = SignalFormatter().format_initial_signal(
        {
            "symbol": "EURUSD", "signal_type": "LONG",
            "entry_price": 1.085, "sl_price": 1.0833,
            "tp1_price": 1.08755, "tp2_price": 1.0901,
        }
    )
    assert "1.08500" in message and "1.08330" in message


def test_auto_quarantine_blocks_strategy() -> None:
    orchestrator = PulseOrchestrator(
        repository_factory=MagicMock(), memory_profiler=MagicMock()
    )
    repository = MagicMock()
    repository.get_kv.return_value = '["PULLBACK_H2L2"]'
    orchestrator._load_auto_quarantine(repository)
    assert orchestrator._strategy_allowed({"strategy": "PULLBACK_H2L2"}) is False
    assert orchestrator._strategy_allowed({"strategy": "PIN_BAR"}) is True


def test_fx_strategy_entry_buffer_scaled() -> None:
    from src.strategies.pullback_h2 import PullbackH2L2Strategy

    bar = Candle(
        symbol="EURUSD", timeframe="M5", timestamp=KILLZONE_TS,
        open=1.0845, high=1.0850, low=1.0840, close=1.0848, volume=0.0,
    )
    setup = PullbackH2L2Strategy()._build(bar, "LONG", "H2_PULLBACK")
    # The old $0.50 gold buffer produced impossible 1.585 entries on FX.
    assert abs(setup["entry_price"] - 1.0852) < 1e-9
    assert abs(setup["sl_price"] - 1.0838) < 1e-9

    gold_bar = Candle(
        symbol="XAUUSD", timeframe="M5", timestamp=KILLZONE_TS,
        open=2400.0, high=2401.0, low=2399.0, close=2400.5, volume=100.0,
    )
    gold_setup = PullbackH2L2Strategy()._build(gold_bar, "LONG", "H2_PULLBACK")
    assert gold_setup["entry_price"] == 2401.5  # legacy $0.50 preserved


def test_fx_sweep_detection_without_volume() -> None:
    from src.analysis.liquidity import LiquiditySweepDetector

    sweep_bar = Candle(
        symbol="EURUSD", timeframe="M5", timestamp=KILLZONE_TS,
        open=1.0850, high=1.0852, low=1.0830, close=1.0849, volume=0.0,
    )
    sweep = LiquiditySweepDetector().detect_sweep(
        current_candle=sweep_bar, avg_volume=0.0,
        last_swing_high=1.0900, last_swing_low=1.0840,
    )
    assert sweep is not None and sweep["type"] == "LIQUIDITY_SWEEP_LONG"

    gold_bar = Candle(
        symbol="XAUUSD", timeframe="M5", timestamp=KILLZONE_TS,
        open=2400.0, high=2401.0, low=2390.0, close=2400.0, volume=0.0,
    )
    # Gold still requires a volume spike (volume=0 -> no sweep).
    assert (
        LiquiditySweepDetector().detect_sweep(
            current_candle=gold_bar, avg_volume=0.0,
            last_swing_high=2450.0, last_swing_low=2395.0,
        )
        is None
    )


def test_multi_symbol_pulse_namespaces_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SYMBOLS", "XAUUSD,BTCUSD")

    db_path = tmp_path / "sprint50.db"
    connection = sqlite3.connect(str(db_path))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)

    candles = {
        "XAUUSD": _candle("XAUUSD", 1_700_000_060),
        "BTCUSD": Candle(
            symbol="BTCUSD", timeframe="M1", timestamp=1_700_000_060,
            open=64000.0, high=64100.0, low=63900.0, close=64050.0, volume=500.0,
        ),
    }

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            return [candles[symbol]]

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repository,
        client_factory=lambda _: StubClient(),
        memory_profiler=MagicMock(),
        telegram_client_factory=lambda: MagicMock(chat_id=None),
    )
    orchestrator._run_macro_regime_check = MagicMock()

    orchestrator.run()

    # Per-symbol watermarks written for both markets.
    assert repository.get_kv("last_processed_XAUUSD") is not None
    assert repository.get_kv("last_processed_BTCUSD") is not None
    # Gold keeps the legacy global watermark; structure state is namespaced.
    assert repository.get_kv("last_processed_timestamp") is not None
    assert repository.get_kv("current_structure_state") is not None
    assert repository.get_kv("current_structure_state:BTCUSD") is not None
    # Both symbols persisted candles.
    gold_rows = repository.get_recent_candles("XAUUSD", "M1", 5)
    btc_rows = repository.get_recent_candles("BTCUSD", "M1", 5)
    assert len(gold_rows) == 1
    assert len(btc_rows) == 1

    repository.close()


if __name__ == "__main__":
    test_instrument_registry_and_state_keys()
    print("Sprint 50 Multi-Asset Engine Verified")
