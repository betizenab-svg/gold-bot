from __future__ import annotations

import logging
import os
import time
from typing import Callable, List, Optional

from config.database import get_connection
from src.core.telemetry import MemoryProfiler
from src.ingestion.factory import get_market_data_client
from src.ingestion.twelvedata import DataIngestionError as TwelveDataIngestionError
from src.ingestion.yahoo_client import DataIngestionError as YahooDataIngestionError
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.validation.validator import DataValidator
from src.domain.candle import Candle
from config.settings import TIMEFRAME_SECONDS


class PulseOrchestrator:
    def __init__(
        self,
        repository_factory: Optional[Callable[[], Repository]] = None,
        client_factory: Optional[Callable[[Repository], object]] = None,
        memory_profiler: Optional[MemoryProfiler] = None,
    ) -> None:
        self.repository_factory = repository_factory or self._default_repository_factory
        self.client_factory = client_factory or get_market_data_client
        self.memory_profiler = memory_profiler or MemoryProfiler()

    def _default_repository_factory(self) -> Repository:
        connection = get_connection()
        SchemaInitializer(connection).initialize()
        return Repository(connection)

    def _mock_client(self, repository: Repository):
        symbol = os.getenv("MOCK_SYMBOL", "XAUUSD")
        timeframe = os.getenv("MOCK_TIMEFRAME", "M1")
        candles_per_run = int(os.getenv("MOCK_CANDLES_PER_RUN", "1"))
        delay_seconds = float(os.getenv("MOCK_DELAY_SECONDS", "0"))
        start_timestamp = int(os.getenv("MOCK_START_TIMESTAMP", str(int(time.time()))))

        class MockClient:
            def fetch_latest_candles(self, _symbol: str, _timeframe: str) -> List[Candle]:
                last_ts_value = repository.get_kv("last_processed_timestamp")
                try:
                    last_ts = int(last_ts_value) if last_ts_value is not None else start_timestamp
                except ValueError:
                    last_ts = start_timestamp

                step = TIMEFRAME_SECONDS.get(timeframe, 60)
                candles: List[Candle] = []
                for i in range(candles_per_run):
                    ts = last_ts + step * (i + 1)
                    candles.append(
                        Candle(
                            symbol=symbol,
                            timeframe=timeframe,
                            timestamp=ts,
                            open=2000.0,
                            high=2001.0,
                            low=1999.0,
                            close=2000.5,
                            volume=100.0,
                        )
                    )

                if delay_seconds > 0:
                    time.sleep(delay_seconds)

                return candles

        return MockClient()

    def run(self) -> None:
        logging.info("---- Pulse started ----")
        start_time = time.time()
        self.memory_profiler.log_snapshot("Pulse start")

        repository = self.repository_factory()
        try:
            if os.getenv("MOCK_INGESTION") == "1":
                client = self._mock_client(repository)
            else:
                client = self.client_factory(repository)
            logging.info("Selected ingestion client: %s", client.__class__.__name__)
            validator = DataValidator()

            try:
                candles = client.fetch_latest_candles("XAUUSD", "H1")
            except (YahooDataIngestionError, TwelveDataIngestionError) as exc:
                logging.error("Ingestion failed: %s", exc)
                return

            self.memory_profiler.log_snapshot("Post-ingestion")

            total_count = len(candles)
            valid_candles = validator.filter_candles(candles)
            filtered = total_count - len(valid_candles)
            logging.info("Candles received: %s, valid: %s", total_count, len(valid_candles))
            if filtered:
                logging.info("Filtered out %s invalid candles", filtered)

            if not valid_candles:
                logging.info("No valid candles to persist; pulse stopping")
                return

            repository.save_candles(valid_candles)
            latest_timestamp = max(candle.timestamp for candle in valid_candles)
            repository.set_kv("last_processed_timestamp", latest_timestamp)

            elapsed = time.time() - start_time
            self.memory_profiler.log_snapshot("Pulse end")
            logging.info("Pulse finished in %.2fs", elapsed)
        finally:
            if hasattr(repository, "connection"):
                repository.connection.close()
            logging.info("---- Pulse ended ----")
