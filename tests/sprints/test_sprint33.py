import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from src.core.logger import StructuredLogger
from src.core.orchestrator import PulseOrchestrator
from src.core.telemetry import MemoryProfiler
from src.domain.candle import Candle


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


def test_telemetry_engine() -> None:
    profiler = MemoryProfiler()
    profiler.start_timer()
    time.sleep(0.2)
    elapsed_ms = profiler.stop_timer()
    assert elapsed_ms >= 200.0

    peak_memory_mb = profiler.get_peak_memory_mb()
    assert isinstance(peak_memory_mb, float)
    assert peak_memory_mb >= 0.0


def test_structured_logger() -> None:
    log_path = Path("logs/test_telemetry.jsonl")
    if log_path.exists():
        log_path.unlink()

    logger = StructuredLogger(log_path)
    payload = {
        "timestamp": "2023-10-27T10:00:00Z",
        "execution_time_ms": 450.5,
        "peak_memory_mb": 45.2,
        "signals_generated": 1,
        "errors_encountered": 0,
    }
    logger.log_pulse_telemetry(payload)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == payload

    log_path.unlink()


def test_orchestrator_integration_mocked() -> None:
    candles = [
        _make_candle(1_700_000_000 + (index * 60), 2000.0, 2005.0, 1995.0, 2001.0)
        for index in range(15)
    ]

    repo_mock = MagicMock()
    repo_mock.connection = MagicMock()
    repo_mock.get_recent_candles.return_value = candles
    repo_mock.get_open_signals.return_value = []

    class StubClient:
        def fetch_latest_candles(self, symbol: str, timeframe: str) -> list[Candle]:
            assert symbol == "XAUUSD"
            assert timeframe == "M1"
            return candles

    structured_logger = MagicMock()
    memory_profiler = MagicMock()
    memory_profiler.stop_timer.return_value = 321.5
    memory_profiler.get_peak_memory_mb.return_value = 45.2

    orchestrator = PulseOrchestrator(
        repository_factory=lambda: repo_mock,
        client_factory=lambda repo: StubClient(),
        memory_profiler=memory_profiler,
        structured_logger=structured_logger,
    )
    orchestrator._run_macro_regime_check = MagicMock()
    orchestrator._monitor_open_signals = MagicMock()
    orchestrator._evaluate_zone_lifecycle = MagicMock()
    orchestrator._persist_latest_fractals = MagicMock()
    orchestrator._evaluate_liquidity_sweep = MagicMock()
    orchestrator._evaluate_market_structure = MagicMock(return_value=None)
    orchestrator._scan_for_fvg_zones = MagicMock(return_value=[])
    orchestrator._scan_for_order_blocks = MagicMock()
    orchestrator._detect_trade_setup = MagicMock(return_value=None)

    orchestrator.run()

    structured_logger.log_pulse_telemetry.assert_called_once()
    telemetry_payload = structured_logger.log_pulse_telemetry.call_args.args[0]
    for key in (
        "timestamp",
        "execution_time_ms",
        "peak_memory_mb",
        "signals_generated",
        "errors_encountered",
    ):
        assert key in telemetry_payload


def main() -> None:
    test_telemetry_engine()
    test_structured_logger()
    test_orchestrator_integration_mocked()
    print("Sprint 33 Telemetry & Logging Verified")


if __name__ == "__main__":
    main()
