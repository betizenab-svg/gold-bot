from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, cast

import pandas as pd

from config.database import get_connection
from src.core.telemetry import MemoryProfiler
from src.analysis.crisis import CrisisDetector
from src.analysis.regime import RegimeDetector
from src.analysis.sovereign import SovereignProxy
from src.analysis.cot_index import CotAnalyzer
from src.analysis.consensus import SurpriseFactorEngine
from src.ingestion.factory import get_market_data_client
from src.ingestion.macro_client import FredMacroClient, MacroDataError
from src.ingestion.cot_client import CotClient
from src.ingestion.calendar_client import EconomicCalendarClient
from src.ingestion.twelvedata import DataIngestionError as TwelveDataIngestionError
from src.ingestion.yahoo_client import DataIngestionError as YahooDataIngestionError
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.validation.validator import DataValidator
from src.domain.candle import Candle
from config.settings import DXY_TICKER, DXY_CORRELATION_WINDOW, TIMEFRAME_SECONDS


MACRO_CACHE_TTL_SECONDS = 86400  # 24 hours
MACRO_HISTORY_DAYS = 90
DXY_HISTORY_DAYS = 30


class PulseOrchestrator:
    def __init__(
        self,
        repository_factory: Optional[Callable[[], Repository]] = None,
        client_factory: Optional[Callable[[Repository], Any]] = None,
        memory_profiler: Optional[MemoryProfiler] = None,
        macro_client: Optional[FredMacroClient] = None,
        regime_detector: Optional[RegimeDetector] = None,
        sovereign_proxy: Optional[SovereignProxy] = None,
        crisis_detector: Optional[CrisisDetector] = None,
    ) -> None:
        self.repository_factory = repository_factory or self._default_repository_factory
        self.client_factory = client_factory or get_market_data_client
        self.memory_profiler = memory_profiler or MemoryProfiler()
        self.macro_client = macro_client or FredMacroClient()
        self.regime_detector = regime_detector or RegimeDetector()
        self.sovereign_proxy = sovereign_proxy or SovereignProxy()
        self.crisis_detector = crisis_detector or CrisisDetector()

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

    def _fetch_gold_daily_closes(self, repository: Repository) -> pd.Series:
        """Aggregate H1 candles from market_data into daily closes."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=MACRO_HISTORY_DAYS)
        cutoff_ts = int(cutoff.timestamp())

        rows = repository.connection.execute(
            """
            SELECT timestamp, close FROM market_data
            WHERE symbol = 'XAUUSD' AND timestamp >= ?
            ORDER BY timestamp ASC;
            """,
            (cutoff_ts,),
        ).fetchall()

        if not rows:
            return pd.Series(dtype=float)

        timestamps = [datetime.fromtimestamp(r[0], tz=timezone.utc).date() for r in rows]
        closes = [float(r[1]) for r in rows]

        frame = pd.DataFrame({"date": timestamps, "close": closes})
        daily = frame.groupby("date")["close"].last()
        daily.index = pd.DatetimeIndex(daily.index)

        return cast(pd.Series, daily)

    def _fetch_dxy_daily_closes(self) -> pd.Series:
        """Fetch DXY daily closing prices from Yahoo Finance."""
        import yfinance as yf

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DXY_HISTORY_DAYS)

        try:
            frame = yf.download(
                tickers=DXY_TICKER,
                start=start,
                end=end,
                interval="1d",
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception as exc:
            logging.error("DXY data fetch failed: %s", exc)
            return pd.Series(dtype=float)

        if frame is None or frame.empty:
            logging.warning("Yahoo Finance returned empty DXY data")
            return pd.Series(dtype=float)

        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)

        closes: pd.Series = cast(pd.Series, frame["Close"]).dropna()

        dti = pd.DatetimeIndex(closes.index)
        if hasattr(dti, "tz") and dti.tz is not None:
            closes.index = dti.tz_localize(None)

        normalized_dates: Any = pd.to_datetime(closes.index)
        closes.index = pd.DatetimeIndex(normalized_dates.floor("D"))

        logging.info("Fetched %d DXY daily closes", len(closes))
        return closes

    def _run_macro_regime_check(self, repository: Repository) -> None:
        """Run the macro regime detection, gated behind a 24-hour cache."""
        now = int(time.time())

        last_update_raw = repository.get_kv("last_macro_update_timestamp")
        if last_update_raw is not None:
            try:
                last_update = int(last_update_raw)
            except ValueError:
                last_update = 0
            if (now - last_update) < MACRO_CACHE_TTL_SECONDS:
                logging.info("Macro regime cache is fresh; skipping update")
                return

        logging.info("Running macro regime detection...")

        gold_series = self._fetch_gold_daily_closes(repository)
        if gold_series.empty:
            logging.warning("No Gold daily data available for regime detection")
            return

        tips_series = self.macro_client.fetch_10y_tips_yield(days=MACRO_HISTORY_DAYS)

        correlation = self.regime_detector.calculate_correlation(
            gold_series, tips_series
        )
        regime = self.regime_detector.determine_regime(correlation)

        repository.set_kv("macro_regime", regime)
        repository.set_kv("macro_tips_correlation", str(correlation))
        repository.set_kv("last_macro_update_timestamp", str(now))

        logging.info(
            "Macro regime updated: %s (correlation=%.4f)", regime, correlation
        )

        # --- Sovereign Demand Proxy ---
        net_purchases = self.sovereign_proxy.get_net_purchases(repository)
        multiplier = self.sovereign_proxy.calculate_multiplier(net_purchases)
        repository.set_kv("macro_long_bias_multiplier", str(multiplier))
        logging.info(
            "Sovereign proxy: net_purchases=%.1f, long_bias_multiplier=%.2f",
            net_purchases,
            multiplier,
        )

        # --- Crisis Filter (DXY Correlation) ---
        gold_daily_for_dxy = self._fetch_gold_daily_closes(repository)
        dxy_series = self._fetch_dxy_daily_closes()
        if not gold_daily_for_dxy.empty and not dxy_series.empty:
            dxy_corr = self.crisis_detector.calculate_dxy_correlation(
                gold_daily_for_dxy, dxy_series
            )
            crisis_mode = self.crisis_detector.evaluate_crisis_mode(dxy_corr)
            repository.set_kv("macro_dxy_correlation", str(dxy_corr))
            repository.set_kv("macro_crisis_mode", str(int(crisis_mode)))
            logging.info(
                "Crisis filter: dxy_correlation=%.4f, crisis_mode=%s",
                dxy_corr,
                crisis_mode,
            )
        else:
            logging.warning("Insufficient data for crisis filter")

        # --- Commitment of Traders (COT) Index ---
        try:
            cot_client = CotClient()
            historical_nets = cot_client.fetch_historical_net_positions()
            
            if historical_nets:
                current_net = historical_nets[-1]
                analyzer = CotAnalyzer()
                
                index_val = analyzer.calculate_index(current_net, historical_nets)
                positioning_state = analyzer.evaluate_positioning(index_val)
                
                repository.set_kv("macro_cot_index", f"{index_val:.2f}")
                repository.set_kv("macro_cot_state", positioning_state)
                logging.info("COT Index: %.2f (%s)", index_val, positioning_state)
        except Exception as exc:
            logging.error("Failed to update COT Index: %s", exc)

        # --- Consensus Variance (Surprise Factor) ---
        try:
            cal_client = EconomicCalendarClient()
            events = cal_client.fetch_latest_events()
            engine = SurpriseFactorEngine()

            max_abs_surprise = 0.0
            overall_state = "NEUTRAL"

            for event in events:
                state = engine.evaluate_double_whammy(event)
                surprise = engine.calculate_surprise_factor(
                    float(event["actual"]),
                    float(event["forecast"]),
                    float(event["historical_sigma"]),
                )
                if abs(surprise) > max_abs_surprise:
                    max_abs_surprise = abs(surprise)
                if state != "NEUTRAL":
                    overall_state = state

            repository.set_kv("macro_surprise_factor", f"{max_abs_surprise:.2f}")
            repository.set_kv("macro_consensus_state", overall_state)
            logging.info(
                "Surprise Factor: %.2f (%s)", max_abs_surprise, overall_state
            )
        except Exception as exc:
            logging.error("Failed to update Surprise Factor: %s", exc)

    def run(self) -> None:
        logging.info("---- Pulse started ----")
        start_time = time.time()
        self.memory_profiler.log_snapshot("Pulse start")

        repository = self.repository_factory()
        try:
            # --- Macro Regime Check (24-hour gated) ---
            try:
                self._run_macro_regime_check(repository)
            except MacroDataError as exc:
                logging.error("Macro regime check failed: %s", exc)
            except Exception as exc:
                logging.error("Unexpected macro regime error: %s", exc)
            if os.getenv("MOCK_INGESTION") == "1":
                client: Any = self._mock_client(repository)
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
