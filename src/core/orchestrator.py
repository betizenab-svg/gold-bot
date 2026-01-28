from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from config.database import get_connection
from src.core.telemetry import MemoryProfiler
from src.ingestion.factory import get_market_data_client
from src.ingestion.oanda import DataIngestionError as OandaDataIngestionError
from src.ingestion.twelvedata import DataIngestionError as TwelveDataIngestionError
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.validation.validator import DataValidator


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

    def run(self) -> None:
        logging.info("---- Pulse started ----")
        start_time = time.time()
        self.memory_profiler.log_snapshot("Pulse start")

        repository = self.repository_factory()
        try:
            client = self.client_factory(repository)
            logging.info("Selected ingestion client: %s", client.__class__.__name__)
            validator = DataValidator()

            try:
                candles = client.fetch_latest_candles("XAUUSD", "H1")
            except (OandaDataIngestionError, TwelveDataIngestionError) as exc:
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
