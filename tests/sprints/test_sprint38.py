from __future__ import annotations

import os
from random import uniform
from unittest.mock import patch

from src.domain.candle import Candle


def _dummy_candles(count: int = 50) -> list[Candle]:
    base_ts = 1_700_000_000
    output: list[Candle] = []
    for idx in range(count):
        close = 2000.0 + (idx * 0.1)
        output.append(
            Candle(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=base_ts + (idx * 60),
                open=close - 0.2,
                high=close + 0.4,
                low=close - 0.4,
                close=close,
                volume=100.0,
            )
        )
    return output


def test_documentation_presence(project_root: str) -> None:
    architecture = os.path.join(project_root, "docs", "architecture.md")
    strategies = os.path.join(project_root, "docs", "strategies.md")

    assert os.path.exists(architecture), "docs/architecture.md is missing"
    assert os.path.exists(strategies), "docs/strategies.md is missing"

    with open(architecture, "r", encoding="utf-8") as handle:
        architecture_text = handle.read()
    with open(strategies, "r", encoding="utf-8") as handle:
        strategies_text = handle.read()

    assert len(architecture_text) > 100
    assert len(strategies_text) > 100


def test_settings_centralization() -> None:
    from config import settings

    assert hasattr(settings, "ATR_SL_MULTIPLIER")
    assert hasattr(settings, "PIN_BAR_TAIL_RATIO")
    assert hasattr(settings, "VALUE_AREA_SMA")


def test_optimizer_logic_mocked() -> None:
    import scripts.optimize_params as optimize_params

    fake_candles = _dummy_candles(50)

    def fake_report(*args, **kwargs):
        return {
            "total_trades": 10,
            "win_rate_pct": 55.0,
            "final_balance": 10000.0 + uniform(-500.0, 1500.0),
        }

    with patch(
        "scripts.optimize_params.CSVDataClient.load_data",
        return_value=fake_candles,
    ), patch(
        "scripts.optimize_params.BacktestEngine.run_simulation",
        return_value=None,
    ) as run_sim_mock, patch(
        "scripts.optimize_params.BacktestEngine.generate_report",
        side_effect=fake_report,
    ):
        rc = optimize_params.main([])

    assert rc == 0
    assert run_sim_mock.call_count == 27


def main() -> int:
    project_root = os.path.abspath(os.path.dirname(__file__))
    test_documentation_presence(project_root)
    test_settings_centralization()
    test_optimizer_logic_mocked()
    print("Sprint 38 Documentation & Parameter Optimization Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
