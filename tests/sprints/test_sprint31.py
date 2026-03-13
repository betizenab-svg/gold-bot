from src.analysis.position_sizing import LotSizeCalculator
from src.analysis.signal_factory import SignalFactory


def test_pip_calculation() -> None:
    calculator = LotSizeCalculator()
    assert calculator.calculate_pips(entry_price=2010.50, sl_price=2008.00) == 25.0


def test_lot_size_math() -> None:
    calculator = LotSizeCalculator()

    assert calculator.calculate_lot_size(balance=100.0, pips=25.0, risk_pct=0.02) == 0.01
    assert calculator.calculate_lot_size(balance=5000.0, pips=25.0, risk_pct=0.02) == 0.40


def test_table_generation() -> None:
    calculator = LotSizeCalculator()
    table = calculator.generate_table(entry_price=2010.50, sl_price=2008.00)

    assert "50000" in table
    assert "0.40" in table
    assert "<b>Baseline Assumption ($100):</b>" in table
    assert "<pre>" in table


def test_signal_factory_reasoning_includes_table() -> None:
    signal = SignalFactory().build_signal(
        symbol="XAUUSD",
        trade_direction="LONG",
        zone_dict={"id": 1, "price_top": 2010.50, "price_bottom": 2008.00},
        atr=2.0,
        score=85,
        timestamp=1_700_000_000,
    )

    assert SignalFactory.LOT_SIZE_TABLE_MARKER in signal.reasoning
    assert "<b>Baseline Assumption ($100):</b>" in signal.reasoning


def main() -> None:
    test_pip_calculation()
    test_lot_size_math()
    test_table_generation()
    test_signal_factory_reasoning_includes_table()
    print("Sprint 31 Dynamic Position Sizing Verified")


if __name__ == "__main__":
    main()
