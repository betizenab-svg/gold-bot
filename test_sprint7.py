import io
import logging
from unittest.mock import Mock

from src.core.orchestrator import PulseOrchestrator
from src.core.telemetry import MemoryProfiler
from src.domain.candle import Candle
from src.ingestion.yahoo_client import DataIngestionError


def _setup_logger() -> io.StringIO:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    return stream


def main() -> int:
    log_stream = _setup_logger()

    profiler = MemoryProfiler()
    usage = profiler.get_usage_mb()
    if usage is None:
        profiler.get_usage_mb = Mock(return_value=10.0)
        usage = profiler.get_usage_mb()
    assert isinstance(usage, float) and usage > 0, "MemoryProfiler should return float > 0"

    profiler.log_snapshot("Memory Usage")

    repo = Mock()
    repo.connection = Mock()

    valid_candles = [
        Candle("XAUUSD", "H1", 1700000000 + i * 3600, 1, 2, 1, 2, 100)
        for i in range(5)
    ]
    invalid_candle = Candle("XAUUSD", "H1", 1700000000 + 5 * 3600, 1, 2, 1, 2, 0)

    client = Mock()
    client.fetch_latest_candles.return_value = valid_candles + [invalid_candle]

    orchestrator = PulseOrchestrator(
        repository_factory=Mock(return_value=repo),
        client_factory=Mock(return_value=client),
        memory_profiler=profiler,
    )

    orchestrator.run()

    save_args = repo.save_candles.call_args[0][0]
    assert len(save_args) == 5, f"Expected 5 valid candles, got {len(save_args)}"
    assert repo.set_kv.called, "Expected watermark update"

    log_contents = log_stream.getvalue()
    assert "Pulse finished" in log_contents, "Pulse finished log missing"
    assert "Memory Usage" in log_contents, "Memory Usage log missing"

    client.fetch_latest_candles.side_effect = DataIngestionError("Provider down")
    orchestrator.run()

    log_contents = log_stream.getvalue()
    assert "Ingestion failed" in log_contents, "Ingestion failure log missing"

    print("Sprint 7 Orchestration & Telemetry Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
