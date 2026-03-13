import sqlite3

from src.alerting.formatter import SignalFormatter
from src.analysis.position_sizing import LotSizeCalculator
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.persistence.schema import SchemaInitializer
from src.strategies.big_bulls_bears import BigBullsBearsStrategy


def _make_candle(
    timestamp: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _build_bullish_strategy_fixture() -> list[Candle]:
    candles: list[Candle] = []
    base_timestamp = 1_700_000_000

    for index in range(198):
        close_price = 100.0 + index
        open_price = close_price - 0.4
        candles.append(
            _make_candle(
                timestamp=base_timestamp + (index * 60),
                open_price=open_price,
                high=close_price + 0.4,
                low=open_price - 0.4,
                close=close_price,
            )
        )

    candles.append(
        _make_candle(
            timestamp=base_timestamp + (198 * 60),
            open_price=299.0,
            high=300.0,
            low=288.0,
            close=293.0,
        )
    )
    candles.append(
        _make_candle(
            timestamp=base_timestamp + (199 * 60),
            open_price=292.0,
            high=302.0,
            low=291.0,
            close=301.0,
        )
    )
    return candles


def test_lot_size_calculator() -> None:
    calculator = LotSizeCalculator()
    table = calculator.calculate_table(sl_distance_pips=50.0)

    assert "$100" in table
    assert "$50000" in table
    assert "BASELINE" in table


def test_signal_formatter() -> None:
    formatter = SignalFormatter()
    signal = Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2000.5,
        sl_price=1990.0,
        tp1_price=2010.0,
        tp2_price=2020.0,
        score=88,
        reasoning="Trend aligned engulfing retracement.",
        timestamp=1_700_000_000,
        signal_hash="abc123",
    )

    initial_signal = formatter.format_initial_signal(signal)
    expected = (
        "Market execution/pending order\n"
        "entry @ 2000.50\n"
        "sl @ 1990.00\n"
        "tp 1 @ 2010.00\n"
        "tp 2 @ 2020.00"
    )
    assert initial_signal == expected

    alert_message, explanation_message = formatter.format_lifecycle_update(
        "TP1_SMASH",
        "Price hit key resistance",
    )
    assert isinstance(alert_message, str)
    assert isinstance(explanation_message, str)
    assert "Price hit key resistance" in explanation_message


def test_strategy_logic() -> None:
    strategy = BigBullsBearsStrategy()
    candles = _build_bullish_strategy_fixture()

    assert strategy.detect_setup(candles[:50]) is None

    setup = strategy.detect_setup(candles)
    assert setup is not None
    assert setup["trade_direction"] == "LONG"
    assert setup["entry_price"] == 301.0
    assert setup["stop_loss"] == 291.0


def test_schema_telegram_columns() -> None:
    connection = sqlite3.connect(":memory:")
    SchemaInitializer(connection).initialize()

    rows = connection.execute("PRAGMA table_info(signals);").fetchall()
    column_names = {row[1] for row in rows}
    assert "telegram_message_id" in column_names
    assert "telegram_chat_id" in column_names
    assert "closure_reason" in column_names


def main() -> None:
    test_lot_size_calculator()
    test_signal_formatter()
    test_strategy_logic()
    test_schema_telegram_columns()
    print("Sprint 25 Strategy & Formatting Verified")


if __name__ == "__main__":
    main()
