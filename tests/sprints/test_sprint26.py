from config.settings import ENTRY_BUFFER_PTS, PIN_BAR_TAIL_RATIO
from src.analysis.signal_factory import SignalFactory
from src.domain.candle import Candle
from src.strategies.pin_bar_rejection import PinBarRejectionStrategy


def _make_candle(
    open_price: float,
    close_price: float,
    high_price: float,
    low_price: float,
    timestamp: int = 1_700_000_000,
) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=timestamp,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=100.0,
    )


def test_pin_bar_recognition() -> Candle:
    strategy = PinBarRejectionStrategy()

    bullish_pin_bar = _make_candle(
        open_price=2008.00,
        close_price=2010.00,
        high_price=2010.00,
        low_price=2000.00,
    )
    assert strategy.is_valid_pin_bar(bullish_pin_bar) == "BULLISH"

    invalid_pin_bar = _make_candle(
        open_price=2005.00,
        close_price=2010.00,
        high_price=2010.00,
        low_price=2000.00,
        timestamp=1_700_000_060,
    )
    assert strategy.is_valid_pin_bar(invalid_pin_bar) is None

    strict_rejection = _make_candle(
        open_price=2006.50,
        close_price=2010.00,
        high_price=2010.00,
        low_price=2000.00,
        timestamp=1_700_000_120,
    )
    assert strategy.is_valid_pin_bar(strict_rejection) is None
    return bullish_pin_bar


def test_setup_and_buffer_calculation_long(valid_bullish_pin_bar: Candle) -> None:
    strategy = PinBarRejectionStrategy()
    active_zones = [
        {
            "id": 101,
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "type": "OB_BULLISH",
            "price_top": 2001.00,
            "price_bottom": 1999.50,
            "status": "ACTIVE",
        }
    ]

    setup = strategy.detect_setup([valid_bullish_pin_bar], active_zones)
    assert setup is not None
    assert setup["trade_direction"] == "LONG"
    assert setup["entry_price"] == 2010.50
    assert setup["sl_price"] == 1999.50
    assert setup["zone_id"] == 101

    signal = SignalFactory().build_signal(
        symbol="XAUUSD",
        trade_direction=setup["trade_direction"],
        zone_dict={**active_zones[0], **setup},
        atr=8.0,
        score=80,
        timestamp=valid_bullish_pin_bar.timestamp,
    )
    assert signal.entry_price == 2010.50
    assert signal.sl_price == 1999.50


def test_setup_rejection_no_zone(valid_bullish_pin_bar: Candle) -> None:
    strategy = PinBarRejectionStrategy()
    assert strategy.detect_setup([valid_bullish_pin_bar], []) is None


def main() -> None:
    assert PIN_BAR_TAIL_RATIO == 0.66
    assert ENTRY_BUFFER_PTS == 0.50
    valid_bullish_pin_bar = test_pin_bar_recognition()
    test_setup_and_buffer_calculation_long(valid_bullish_pin_bar)
    test_setup_rejection_no_zone(valid_bullish_pin_bar)
    print("Sprint 26 Pin Bar Rejection Strategy Verified")


if __name__ == "__main__":
    main()
