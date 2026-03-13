from unittest.mock import MagicMock

from src.alerting.formatter import SignalFormatter
from src.alerting.lifecycle_manager import LifecycleManager
from src.analysis.position_sizing import LotSizeCalculator
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.strategies.inside_bar_trap import InsideBarTrapStrategy


def _make_candle(
    timestamp: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100.0,
    )


def test_inside_bar_trap_long() -> None:
    strategy = InsideBarTrapStrategy()
    candles = [
        _make_candle(1_700_000_000, 2012.00, 2020.00, 2010.00, 2018.00),
        _make_candle(1_700_000_060, 2016.00, 2018.00, 2012.00, 2014.00),
        _make_candle(1_700_000_120, 2013.00, 2015.00, 2008.00, 2015.00),
    ]

    setup = strategy.detect_setup(candles)
    assert setup is not None
    assert setup["trade_direction"] == "LONG"
    assert setup["entry_price"] == 2015.00
    assert setup["sl_price"] == 2008.00


def test_signal_formatter() -> None:
    formatter = SignalFormatter()
    signal = Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2015.0,
        sl_price=2008.0,
        tp1_price=2025.5,
        tp2_price=2036.0,
        score=81,
        reasoning="Inside bar bear trap reclaimed the mother-bar range.",
        timestamp=1_700_000_120,
        signal_hash="signal-27",
    )

    formatted = formatter.format_initial_signal(signal)
    expected = (
        "Market execution/pending order\n"
        "entry @ 2015.00\n"
        "sl @ 2008.00\n"
        "tp 1 @ 2025.50\n"
        "tp 2 @ 2036.00"
    )
    assert formatted == expected


def test_lot_size_calculator() -> None:
    calculator = LotSizeCalculator()
    table = calculator.calculate_table(50.0)

    for balance in ("$50", "$100", "$200", "$500", "$700", "$1000", "$2000", "$5000", "$10000", "$50000"):
        assert balance in table
    assert "Assumed baseline balance for this signal is $100." in table


def test_trade_reasoning_reply_threading() -> None:
    telegram_client = MagicMock()
    telegram_client.send_message.side_effect = [
        {"result": {"message_id": 12345}},
        {"result": {"message_id": 12346}},
    ]
    repository = MagicMock()
    lifecycle_manager = LifecycleManager(
        telegram_client=telegram_client,
        repository=repository,
    )
    signal = Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2015.0,
        sl_price=2008.0,
        tp1_price=2025.5,
        tp2_price=2036.0,
        score=81,
        reasoning="Inside bar bear trap reclaimed the mother-bar range.",
        timestamp=1_700_000_120,
        signal_hash="signal-27",
        telegram_chat_id="chat-123",
    )

    lifecycle_manager.deploy_signal(signal, sl_distance_pips=50.0)

    assert telegram_client.send_message.call_count == 2
    _, reasoning_call = telegram_client.send_message.call_args_list
    assert reasoning_call.kwargs["reply_to_message_id"] == "12345"
    repository.update_signal_telegram_metadata.assert_called_once_with(
        signal_hash="signal-27",
        telegram_message_id="12345",
        telegram_chat_id="chat-123",
    )


def test_lifecycle_manager_threading() -> None:
    telegram_client = MagicMock()
    telegram_client.send_message.side_effect = [
        {"result": {"message_id": 22345}},
        {"result": {"message_id": 22346}},
    ]
    lifecycle_manager = LifecycleManager(telegram_client=telegram_client)
    signal = Signal(
        symbol="XAUUSD",
        signal_type="LONG",
        entry_price=2015.0,
        sl_price=2008.0,
        tp1_price=2025.5,
        tp2_price=2036.0,
        score=81,
        reasoning="Inside bar bear trap reclaimed the mother-bar range.",
        timestamp=1_700_000_120,
        signal_hash="signal-27",
        telegram_chat_id="chat-123",
        telegram_message_id="67890",
    )

    lifecycle_manager.send_lifecycle_update(
        signal,
        "TP1_SMASH",
        "Price exhausted at Daily Resistance",
    )

    assert telegram_client.send_message.call_count == 2
    first_call, second_call = telegram_client.send_message.call_args_list
    assert first_call.kwargs["reply_to_message_id"] == "67890"
    assert second_call.kwargs["reply_to_message_id"] == "67890"


def main() -> None:
    test_inside_bar_trap_long()
    test_signal_formatter()
    test_lot_size_calculator()
    test_trade_reasoning_reply_threading()
    test_lifecycle_manager_threading()
    print("Sprint 27 Inside Bar Trap & Alerting Pipeline Verified")


if __name__ == "__main__":
    main()
