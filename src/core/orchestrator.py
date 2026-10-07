# pyre-ignore-all-errors[21]
# pyright: reportMissingImports=false
from __future__ import annotations

import html
import json
import logging
import os
import time
from dataclasses import replace as dataclass_replace
# Trigger linter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, cast

import numpy as np
import pandas as pd

from config import settings as app_settings
from config.database import get_connection
from src.core.logger import StructuredLogger
from src.core.telemetry import MemoryProfiler
from src.alerting.lifecycle_manager import LifecycleManager, SignalLifecycleManager
from src.alerting.channels import copy_targets, send_copies, vip_chat_id
from src.alerting.messenger import OutboxFlusher, admin_chat_id, deliver, notify_admin, uses_outbox
from src.alerting.telegram_client import TelegramAPIError, TelegramClient
from src.analysis.outcomes import realized_r_for_event
from src.analysis.crisis import CrisisDetector
from src.analysis.regime import RegimeDetector
from src.analysis.sovereign import SovereignProxy
from src.analysis.cot_index import CotAnalyzer
from src.analysis.consensus import SurpriseFactorEngine
from src.analysis.atr import ATREngine
from src.analysis.displacement import DisplacementEngine
from src.analysis.evidence import baseline as evidence_baseline, disabled_pair, load_evidence, quiet_hour
from src.analysis.filters import PermissionEngine
from src.analysis.fractals import FractalDetector
from src.analysis.fvg import FVGScanner
from src.analysis.liquidity import LiquiditySweepDetector
from src.analysis.mitigation import ZoneLifecycleManager
from src.analysis.order_block import OrderBlockScanner
from src.analysis.signal_factory import SignalFactory
from src.analysis.trend_day import against_trend_day
from src.analysis.market_mood import mood_block_reason
from src.analysis.second_opinion import veto_reason as second_opinion_veto
from src.strategies.range_breakout import AsianRangeBreakoutStrategy, OpeningRangeBreakoutStrategy
from src.analysis.scoring import ScoringEngine
from src.analysis.structure import MarketStructureEngine
from src.ingestion.factory import (
    SOURCE_SPOT,
    feed_kind_key,
    get_market_router,
    spot_feed_configured,
)
from src.ingestion.macro_client import FredMacroClient, MacroDataError
from src.ingestion.cot_client import CotClient
from src.ingestion.calendar_client import EconomicCalendarClient
from src.ingestion.twelvedata import DataIngestionError as TwelveDataIngestionError
from src.ingestion.yahoo_client import DataIngestionError as YahooDataIngestionError
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer
from src.validation.validator import DataValidator
from src.domain.candle import Candle
from config.instruments import active_symbols, get_instrument, is_trial, state_key
from config.settings import (
    ANALYSIS_LOOKBACK_CANDLES,
    AUTO_QUARANTINE_ENABLED,
    CHART_ALERTS_ENABLED,
    CODE_VERSION,
    DAILY_STATUS_ENABLED,
    DAILY_STATUS_HOUR_UTC,
    DISABLED_STRATEGIES,
    DXY_TICKER,
    FEED_LAG_ALERT_MINUTES,
    MARKET_DATA_RETENTION_DAYS,
    NEWS_AUTOFETCH_ENABLED,
    NEWS_BLACKOUT_AFTER_MIN,
    NEWS_BLACKOUT_BEFORE_MIN,
    NEWS_CALENDAR_REFRESH_HOURS,
    OPS_TELEMETRY_ENABLED,
    SHADOW_MAX_AGE_DAYS,
    SHADOW_TRACKING_ENABLED,
    SIGNAL_TIMEFRAME,
    TIMEFRAME_SECONDS,
    FSR_LOOKBACK_PERIOD,
    TWELVEDATA_API_KEY,
    TWELVEDATA_BASE_URL,
    WEEKLY_REPORT_ENABLED,
    WEEKLY_REPORT_INTERVAL_DAYS,
    WEEKLY_REPORT_MIN_TRADES,
)
from src.analysis.fsr_engine import FSREngine
from src.analysis.bias_engine import MacroBiasAggregator
from src.analysis.confluence import ConfluenceEngineV2
from src.analysis.risk_governor import RiskGovernor
from src.analysis.trendline import TrendlineEngine
from src.strategies.engulfing_zone import EngulfingZoneStrategy
from src.strategies.inside_bar_trap import InsideBarTrapStrategy
from src.strategies.pin_bar_rejection import PinBarRejectionStrategy
from src.strategies.pullback_h2 import PullbackH2L2Strategy
from src.strategies.quasimodo import QuasimodoStrategy


MACRO_CACHE_TTL_SECONDS = 86400  # 24 hours
MACRO_HISTORY_DAYS = 90
DXY_HISTORY_DAYS = 30

# Legacy per-status values for rows written before exact results were stored.
STATUS_R = {
    "CLOSED_TP2": 2.25,
    "CLOSED_BE": 0.75,
    "CLOSED_SL": -1.0,
    "CLOSED_TIME": 0.0,
    "CLOSED_STRUCT": 1.0,
}

# Per-market state that refers to price levels; cleared when a market changes feed.
PRICE_STATE_KEYS = (
    "swing_history",
    "smc_latest_fractals",
    "last_swing_high",
    "last_swing_low",
    "latest_liquidity_sweep",
    "current_structure_state",
    "last_setup_attempt",
    "latest_setup_score",
    "latest_setup_classification",
)


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
        telegram_client_factory: Optional[Callable[[], TelegramClient]] = None,
        lifecycle_manager_factory: Optional[Callable[[Repository], LifecycleManager]] = None,
        structured_logger: Optional[StructuredLogger] = None,
    ) -> None:
        self.repository_factory = repository_factory or self._default_repository_factory
        self.client_factory = client_factory or get_market_router
        self.memory_profiler = memory_profiler or MemoryProfiler()
        self.structured_logger = structured_logger
        self.macro_client = macro_client or FredMacroClient()
        self.regime_detector = regime_detector or RegimeDetector()
        self.sovereign_proxy = sovereign_proxy or SovereignProxy()
        self.crisis_detector = crisis_detector or CrisisDetector()
        self.telegram_client_factory = telegram_client_factory or self._default_telegram_client_factory
        self.lifecycle_manager_factory = (
            lifecycle_manager_factory or self._default_lifecycle_manager_factory
        )
        # Strategies quarantined automatically from live results (kv-backed).
        self._extra_disabled: set[str] = set()
        # Per-market price source and lateness seen during the current run.
        self._run_feed: dict[str, dict[str, Any]] = {}

    def _default_repository_factory(self) -> Repository:
        connection = get_connection()
        SchemaInitializer(connection).initialize()
        return Repository(connection)

    def _default_telegram_client_factory(self) -> TelegramClient:
        return TelegramClient()

    def _default_lifecycle_manager_factory(
        self,
        repository: Repository,
    ) -> LifecycleManager:
        return LifecycleManager(
            telegram_client=self.telegram_client_factory(),
            repository=repository,
        )

    def _mock_client(self, repository: Repository):
        symbol = os.getenv("MOCK_SYMBOL", "XAUUSD")
        timeframe = os.getenv("MOCK_TIMEFRAME", SIGNAL_TIMEFRAME)
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
        """Daily gold closes for the macro engines: the spot feed's own daily
        bars when it is configured (one call a day), else stored candles."""
        if spot_feed_configured("XAUUSD"):
            spot_daily = self._spot_daily_closes("XAU/USD")
            if not spot_daily.empty:
                return spot_daily
        cutoff = datetime.now(timezone.utc) - timedelta(days=MACRO_HISTORY_DAYS)
        cutoff_ts = int(cutoff.timestamp())

        rows = repository.get_gold_closes_since(cutoff_ts)

        if not rows:
            return pd.Series(dtype=float)

        timestamps = [datetime.fromtimestamp(r[0], tz=timezone.utc).date() for r in rows]
        closes = [float(r[1]) for r in rows]

        frame = pd.DataFrame({"date": timestamps, "close": closes})
        daily = frame.groupby("date")["close"].last()
        daily.index = pd.DatetimeIndex(daily.index)

        return cast(pd.Series, daily)

    @staticmethod
    def _spot_daily_closes(spot_symbol: str) -> pd.Series:
        import requests

        try:
            response = requests.get(
                f"{TWELVEDATA_BASE_URL.rstrip('/')}/time_series",
                params={
                    "symbol": spot_symbol,
                    "interval": "1day",
                    "outputsize": MACRO_HISTORY_DAYS,
                    "timezone": "UTC",
                    "apikey": TWELVEDATA_API_KEY,
                },
                timeout=15,
            )
            values = response.json().get("values")
        except Exception as exc:
            logging.info("Spot daily closes unavailable: %s", exc)
            return pd.Series(dtype=float)
        if not isinstance(values, list) or not values:
            return pd.Series(dtype=float)
        dates: list[Any] = []
        closes: list[float] = []
        for item in values:
            try:
                dates.append(pd.Timestamp(str(item["datetime"])[:10]))
                closes.append(float(item["close"]))
            except (KeyError, TypeError, ValueError):
                continue
        series = pd.Series(closes, index=pd.DatetimeIndex(dates), dtype=float).sort_index()
        return cast(pd.Series, series[~series.index.duplicated(keep="last")])

    def _fetch_dxy_daily_closes(self) -> pd.Series:
        """Fetch DXY daily closing prices from Yahoo Finance."""
        import yfinance as yf

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DXY_HISTORY_DAYS)

        candidates = [DXY_TICKER, "DX=F", "DX-Y.NYB"]
        frame: pd.DataFrame | None = None
        selected_symbol = ""

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        for ticker in candidates:
            try:
                candidate_frame = yf.download(
                    tickers=ticker,
                    start=start,
                    end=end,
                    interval="1d",
                    progress=False,
                    auto_adjust=False,
                    threads=False,
                )
            except Exception as exc:
                logging.info("DXY fetch failed for %s: %s", ticker, exc)
                continue

            if candidate_frame is None or candidate_frame.empty:
                continue

            frame = candidate_frame
            selected_symbol = ticker
            break

        if frame is None or frame.empty:
            logging.info("Yahoo Finance returned empty DXY data across all symbols")
            return pd.Series(dtype=float)

        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)

        closes: pd.Series = cast(pd.Series, frame["Close"]).dropna()

        dti = pd.DatetimeIndex(closes.index)
        if hasattr(dti, "tz") and dti.tz is not None:
            closes.index = dti.tz_localize(None)

        normalized_dates: Any = pd.to_datetime(closes.index)
        closes.index = pd.DatetimeIndex(normalized_dates.floor("D"))

        logging.info("Fetched %d DXY daily closes via %s", len(closes), selected_symbol)
        return closes

    @staticmethod
    def _align_series(values: List[float], target_length: int) -> List[float]:
        if target_length <= 0 or not values:
            return []
        if len(values) >= target_length:
            return values[-target_length:]
        if len(values) == 1:
            return values * target_length

        x_src = np.arange(len(values), dtype=float)
        x_dst = np.linspace(0.0, float(len(values) - 1), num=target_length)
        return list(np.interp(x_dst, x_src, np.array(values, dtype=float)))

    @staticmethod
    def _calculate_smt_state(
        gold_series: pd.Series,
        dxy_series: pd.Series,
        window: int = 20,
    ) -> tuple[Optional[str], float]:
        """Z-score of gold's spread vs inverted DXY. |z| >= 2 flags a stretched
        divergence that historically mean-reverts (SMT / correlation books)."""
        if gold_series.empty or dxy_series.empty:
            return None, 0.0

        common_dates = gold_series.index.intersection(dxy_series.index)
        if len(common_dates) < 10:
            return None, 0.0

        gold = gold_series.loc[common_dates].tail(window).astype(float)
        dxy = dxy_series.loc[common_dates].tail(window).astype(float)
        if len(gold) < 10 or float(gold.iloc[0]) == 0 or float(dxy.iloc[0]) == 0:
            return None, 0.0

        gold_norm = gold / float(gold.iloc[0])
        dxy_inverted = 2.0 - (dxy / float(dxy.iloc[0]))
        spread = gold_norm - dxy_inverted
        std = float(spread.std())
        if std <= 0:
            return "NEUTRAL", 0.0

        z_score = (float(spread.iloc[-1]) - float(spread.mean())) / std
        if z_score >= 2.0:
            return "GOLD_RICH", z_score
        if z_score <= -2.0:
            return "GOLD_CHEAP", z_score
        return "NEUTRAL", z_score

    @staticmethod
    def _parse_swing_point(raw_value: Optional[str]) -> Optional[dict[str, float | int]]:
        if not raw_value:
            return None

        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        if "timestamp" not in payload or "price" not in payload:
            return None

        try:
            return {
                "timestamp": int(payload["timestamp"]),
                "price": float(payload["price"]),
            }
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_liquidity_sweep(raw_value: Optional[str]) -> Optional[dict[str, Any]]:
        if not raw_value:
            return None

        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        if "timestamp" not in payload or "type" not in payload:
            return None

        try:
            return {
                "timestamp": int(payload["timestamp"]),
                "type": str(payload["type"]),
            }
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _zone_bounce_direction(current_candle: Candle, zone: dict[str, Any]) -> Optional[str]:
        zone_type = str(zone.get("type", "")).upper()
        status = str(zone.get("status", "")).upper()
        if status not in {"ACTIVE", "UNMITIGATED", "MITIGATED"}:
            return None

        try:
            price_top = float(zone.get("price_top"))
            price_bottom = float(zone.get("price_bottom"))
        except (TypeError, ValueError):
            return None

        if "OB_BULLISH" in zone_type:
            touched_zone = float(current_candle.low) <= price_top
            bounced = float(current_candle.close) >= price_top
            if touched_zone and bounced:
                return "LONG"

        if "OB_BEARISH" in zone_type:
            touched_zone = float(current_candle.high) >= price_bottom
            bounced = float(current_candle.close) <= price_bottom
            if touched_zone and bounced:
                return "SHORT"

        return None

    def _strategy_allowed(self, setup: Optional[dict[str, Any]], symbol: Optional[str] = None) -> bool:
        if setup is None:
            return False
        strategy = str(setup.get("strategy", "")).upper()
        if strategy and strategy in DISABLED_STRATEGIES:
            logging.info("Setup from quarantined strategy skipped: %s", strategy)
            return False
        if strategy and strategy in self._extra_disabled:
            logging.info("Setup from auto-quarantined strategy skipped: %s", strategy)
            return False
        if strategy and symbol and disabled_pair(symbol, strategy):
            logging.info("Setup skipped: %s on %s loses in history tests", strategy, symbol)
            return False
        return True

    def _detect_trade_setup(
        self,
        repository: Repository,
        symbol: str,
        current_candle: Candle,
        recent_candles: List[Candle],
        new_candle_count: int = 1,
    ) -> Optional[dict[str, Any]]:
        """Scan every candle that arrived since the last pulse as a potential
        trigger bar (newest first). Scheduler jitter can deliver 3-6 candles
        per pulse; checking only the latest silently skips most triggers."""
        active_zones = repository.get_active_zones(symbol)

        try:
            raw_history = repository.get_kv(state_key("swing_history", symbol))
            swing_history = (
                json.loads(raw_history) if isinstance(raw_history, str) else None
            )
        except (TypeError, ValueError):
            swing_history = None

        scan_count = max(1, min(int(new_candle_count), 12, len(recent_candles)))
        for offset in range(scan_count):
            window = recent_candles if offset == 0 else recent_candles[:-offset]
            if len(window) == 0:
                break
            setup = self._detect_setup_on_window(
                repository, symbol, window, active_zones, swing_history
            )
            if setup is not None:
                return setup
        return None

    def _detect_setup_on_window(
        self,
        repository: Repository,
        symbol: str,
        window: List[Candle],
        active_zones: Any,
        swing_history: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        pin_bar_setup = PinBarRejectionStrategy().detect_setup(window, active_zones)
        if self._strategy_allowed(pin_bar_setup, symbol):
            return pin_bar_setup

        engulfing_setup = EngulfingZoneStrategy().detect_setup(window, active_zones)
        if self._strategy_allowed(engulfing_setup, symbol):
            return engulfing_setup

        pullback_setup = PullbackH2L2Strategy().detect_setup(window)
        if self._strategy_allowed(pullback_setup, symbol):
            return pullback_setup

        if isinstance(swing_history, dict):
            quasimodo_setup = QuasimodoStrategy().detect_setup(
                window, swing_history, active_zones
            )
            if self._strategy_allowed(quasimodo_setup, symbol):
                return quasimodo_setup

        inside_bar_setup = InsideBarTrapStrategy().detect_setup(window)
        if self._strategy_allowed(inside_bar_setup, symbol):
            return inside_bar_setup

        for breakout in (OpeningRangeBreakoutStrategy(), AsianRangeBreakoutStrategy()):
            breakout_setup = breakout.detect_setup(window)
            if self._strategy_allowed(breakout_setup, symbol):
                return breakout_setup

        try:
            order_blocks = repository.get_recent_order_blocks(symbol, limit=20)
        except Exception:
            return None
        if not order_blocks:
            return None

        bounce_candle = window[-1]
        for zone in order_blocks:
            trade_direction = self._zone_bounce_direction(bounce_candle, zone)
            if trade_direction is None:
                continue
            if disabled_pair(symbol, "ZONE_BOUNCE"):
                return None
            return {
                "trade_direction": trade_direction,
                "strategy": "ZONE_BOUNCE",
                "order_type": "LIMIT",
                "zone": zone,
            }

        return None

    @staticmethod
    def _build_macro_permission_context(repository: Repository) -> dict[str, Any]:
        return {
            "macro_cot_state": repository.get_kv("macro_cot_state"),
            "macro_consensus_state": repository.get_kv("macro_consensus_state"),
            "macro_long_bias_multiplier": repository.get_kv("macro_long_bias_multiplier"),
        }

    @staticmethod
    def _has_recent_directional_sweep(
        trade_direction: str,
        current_timestamp: int,
        timeframe: str,
        latest_sweep: Optional[dict[str, Any]],
    ) -> bool:
        if latest_sweep is None:
            return False

        sweep_type = str(latest_sweep.get("type", "")).upper()
        sweep_timestamp = latest_sweep.get("timestamp")
        if not isinstance(sweep_timestamp, int):
            return False

        step_seconds = int(TIMEFRAME_SECONDS.get(timeframe, 60))
        max_age_seconds = 15 * step_seconds
        if current_timestamp - sweep_timestamp > max_age_seconds:
            return False

        return (trade_direction.upper() == "LONG" and sweep_type == "LIQUIDITY_SWEEP_LONG") or (
            trade_direction.upper() == "SHORT" and sweep_type == "LIQUIDITY_SWEEP_SHORT"
        )

    def _persist_latest_fractals(
        self,
        repository: Repository,
        latest_fractals: dict[str, Any],
        symbol: str = "XAUUSD",
    ) -> None:
        repository.set_kv(state_key("smc_latest_fractals", symbol), json.dumps(latest_fractals))

        swing_high = latest_fractals.get("swing_high")
        swing_low = latest_fractals.get("swing_low")

        if swing_high is not None:
            repository.set_kv(state_key("last_swing_high", symbol), swing_high)
        if swing_low is not None:
            repository.set_kv(state_key("last_swing_low", symbol), swing_low)

        # Rolling pivot history feeds the Brooks trendline gate.
        try:
            raw_history = repository.get_kv(state_key("swing_history", symbol))
            history = TrendlineEngine.update_history(
                raw_history if isinstance(raw_history, str) else None,
                latest_fractals,
            )
            repository.set_kv(state_key("swing_history", symbol), json.dumps(history))
        except Exception as exc:
            logging.debug("Swing history update skipped: %s", exc)

    def _evaluate_zone_lifecycle(
        self,
        repository: Repository,
        symbol: str,
        new_candles: List[Candle],
    ) -> None:
        lifecycle_manager = ZoneLifecycleManager()
        for candle in sorted(new_candles, key=lambda item: item.timestamp):
            active_zones = repository.get_active_zones(symbol)
            zones_to_check = list(active_zones) if isinstance(active_zones, list) else []

            # Include once-touched (MITIGATED) order blocks so a second touch
            # consumes them (fresh-zones-only book rule).
            try:
                recent_obs = repository.get_recent_order_blocks(symbol, limit=20)
            except Exception:
                recent_obs = []
            if isinstance(recent_obs, list):
                seen_ids = {zone.get("id") for zone in zones_to_check}
                for zone in recent_obs:
                    if str(zone.get("status", "")).upper() == "MITIGATED" and zone.get("id") not in seen_ids:
                        zones_to_check.append(zone)

            updated_zones = lifecycle_manager.evaluate_zones(candle, zones_to_check)
            if not updated_zones:
                continue

            repository.update_zone_statuses(updated_zones)
            logging.info("Updated %d zones during lifecycle evaluation", len(updated_zones))

    @staticmethod
    def _signal_symbol_matches(signal: Any, symbol: str) -> bool:
        """Only compare a signal against candles of its own market. Non-string
        symbols (mocks/legacy rows) keep the legacy match-everything behavior."""
        sig_symbol = getattr(signal, "symbol", None)
        if not isinstance(sig_symbol, str) or not sig_symbol:
            return True
        return sig_symbol.upper() == str(symbol).upper()

    def _monitor_open_signals(
        self,
        repository: Repository,
        new_candles: List[Candle],
    ) -> int:
        """Walk EVERY new candle since the last pulse (oldest first) so TP/SL
        touches inside scheduler gaps are never skipped. Returns how many
        lifecycle updates could not be delivered."""
        undelivered = 0
        if not new_candles:
            return undelivered
        lifecycle_manager = self.lifecycle_manager_factory(repository)
        for candle in sorted(new_candles, key=lambda item: item.timestamp):
            open_signals = repository.get_open_signals()
            if open_signals is None:
                continue
            if not isinstance(open_signals, list):
                try:
                    open_signals = list(open_signals)
                except TypeError:
                    logging.info("Open signal payload is not iterable; skipping lifecycle monitor")
                    return undelivered
            candle_symbol = getattr(candle, "symbol", None)
            if isinstance(candle_symbol, str) and candle_symbol:
                open_signals = [
                    signal
                    for signal in open_signals
                    if self._signal_symbol_matches(signal, candle_symbol)
                ]
            if not open_signals:
                continue

            result = lifecycle_manager.process_open_signals(
                open_signals=open_signals,
                current_candle=candle,
                telegram_client=lifecycle_manager.telegram_client,
                repository=repository,
                formatter=lifecycle_manager.formatter,
            )
            if isinstance(result, int):
                undelivered += result
        return undelivered

    def _evaluate_market_structure(
        self,
        repository: Repository,
        current_candle: Candle,
        prev_close: Optional[float] = None,
    ) -> Optional[str]:
        structure_engine = MarketStructureEngine()

        raw_symbol = getattr(current_candle, "symbol", None)
        symbol = raw_symbol if isinstance(raw_symbol, str) and raw_symbol else "XAUUSD"
        structure_key = state_key("current_structure_state", symbol)

        stored_trend = repository.get_kv(structure_key)
        current_trend = stored_trend.upper() if stored_trend else "BULLISH"
        if stored_trend is None:
            repository.set_kv(structure_key, current_trend)

        last_swing_high = self._parse_swing_point(
            repository.get_kv(state_key("last_swing_high", symbol))
        )
        last_swing_low = self._parse_swing_point(
            repository.get_kv(state_key("last_swing_low", symbol))
        )

        counter_trend_swing = last_swing_low if current_trend == "BULLISH" else last_swing_high
        new_trend = structure_engine.detect_choch(
            current_candle=current_candle,
            last_counter_trend_swing=counter_trend_swing,
            current_trend=current_trend,
        )
        if new_trend is not None:
            repository.set_kv(structure_key, new_trend)
            logging.info(
                "CHOCH detected: %s -> %s at timestamp=%s close=%.2f",
                current_trend,
                new_trend,
                current_candle.timestamp,
                current_candle.close,
            )
            return None

        trend_swing = last_swing_high if current_trend == "BULLISH" else last_swing_low
        if structure_engine.detect_bos(
            current_candle=current_candle,
            last_swing_point=trend_swing,
            current_trend=current_trend,
            confirmation_close=prev_close,
        ):
            logging.info(
                "BOS detected: trend=%s timestamp=%s close=%.2f",
                current_trend,
                current_candle.timestamp,
                current_candle.close,
            )
            return current_trend

        return None

    def _evaluate_liquidity_sweep(
        self,
        repository: Repository,
        recent_candles: List[Candle],
        new_candles: List[Candle],
    ) -> None:
        if not recent_candles or not new_candles:
            return

        raw_symbol = getattr(new_candles[0], "symbol", None)
        symbol = raw_symbol if isinstance(raw_symbol, str) and raw_symbol else "XAUUSD"

        last_swing_high_point = self._parse_swing_point(
            repository.get_kv(state_key("last_swing_high", symbol))
        )
        last_swing_low_point = self._parse_swing_point(
            repository.get_kv(state_key("last_swing_low", symbol))
        )
        if last_swing_high_point is None or last_swing_low_point is None:
            return

        detector = LiquiditySweepDetector()
        avg_volume = detector.calculate_average_volume(recent_candles, period=14)
        # Every gap candle can be the sweep bar, not just the newest one.
        for candle in sorted(new_candles, key=lambda item: item.timestamp):
            sweep = detector.detect_sweep(
                current_candle=candle,
                avg_volume=avg_volume,
                last_swing_high=float(last_swing_high_point["price"]),
                last_swing_low=float(last_swing_low_point["price"]),
            )
            if sweep is None:
                continue

            repository.set_kv(state_key("latest_liquidity_sweep", symbol), json.dumps(sweep))
            logging.info(
                "Liquidity sweep detected: type=%s sweep_price=%.2f timestamp=%s",
                sweep["type"],
                sweep["sweep_price"],
                sweep["timestamp"],
            )

    def _load_recent_fvgs(
        self,
        repository: Repository,
        symbol: str,
        timeframe: str,
        limit: int = 10,
    ) -> List[dict[str, Any]]:
        try:
            rows = repository.get_recent_unmitigated_fvgs(symbol, timeframe, limit)
        except Exception:
            return []

        if not isinstance(rows, list):
            return []

        return rows

    def _scan_for_fvg_zones(
        self,
        repository: Repository,
        recent_candles: List[Candle],
    ) -> List[dict[str, Any]]:
        if not recent_candles:
            return []

        symbol = recent_candles[-1].symbol
        timeframe = recent_candles[-1].timeframe
        fvg_window = recent_candles[-15:]
        atr_engine = ATREngine()
        current_atr = atr_engine.calculate_atr(fvg_window, period=14)

        scanner = FVGScanner()
        zone = scanner.detect_fvg(fvg_window, current_atr)
        if zone is None:
            return self._load_recent_fvgs(repository, symbol, timeframe)

        zone["created_at"] = int(fvg_window[-1].timestamp)
        repository.save_zone(zone)
        logging.info(
            "FVG detected: type=%s top=%.2f bottom=%.2f status=%s",
            zone["type"],
            zone["price_top"],
            zone["price_bottom"],
            zone["status"],
        )
        return self._load_recent_fvgs(repository, symbol, timeframe)

    def _scan_for_order_blocks(
        self,
        repository: Repository,
        recent_candles: List[Candle],
        recent_bos_type: Optional[str],
        recent_fvgs: List[dict[str, Any]],
    ) -> None:
        if recent_bos_type is None or not recent_candles:
            return

        displacement_engine = DisplacementEngine()
        avg_body = displacement_engine.calculate_average_body(recent_candles, period=14)
        scanner = OrderBlockScanner()
        zone = scanner.detect_order_block(
            candles=recent_candles,
            recent_bos_type=recent_bos_type,
            recent_fvgs=recent_fvgs,
            avg_body=avg_body,
        )
        if zone is None:
            return

        zone["created_at"] = int(recent_candles[-1].timestamp)
        repository.save_zone(zone)
        logging.info(
            "Order block detected: type=%s top=%.2f bottom=%.2f status=%s",
            zone["type"],
            zone["price_top"],
            zone["price_bottom"],
            zone["status"],
        )

    @staticmethod
    def _signal_context(potential_setup: dict[str, Any]) -> dict[str, Any]:
        zone = cast(Optional[dict[str, Any]], potential_setup.get("zone")) or {}
        signal_context: dict[str, Any] = dict(zone)
        for key in (
            "entry_price",
            "sl_price",
            "strategy",
            "trigger",
            "zone_id",
            "order_type",
            "confluence_notes",
            "measured_move",
            "plan_context",
        ):
            if key in potential_setup:
                signal_context[key] = potential_setup[key]
        if "id" not in signal_context and "zone_id" in potential_setup:
            signal_context["id"] = potential_setup["zone_id"]
        return signal_context

    def _hypothetical_levels(
        self,
        potential_setup: dict[str, Any],
        recent_candles: List[Candle],
        symbol: str,
    ) -> Optional[tuple[float, float, float, float]]:
        """Entry/stop/targets the idea would have had, so its outcome can be followed."""
        if not SHADOW_TRACKING_ENABLED or not isinstance(recent_candles, list) or not recent_candles:
            return None
        try:
            atr_value = ATREngine().calculate_atr(recent_candles[-15:], period=14)
            context = self._signal_context(potential_setup)
            context["market_price"] = float(recent_candles[-1].close)
            return SignalFactory().calculate_parameters(
                str(potential_setup.get("trade_direction", "")),
                context,
                atr_value,
                symbol,
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None

    def _persist_actionable_signal(
        self,
        repository: Repository,
        potential_setup: dict[str, Any],
        recent_candles: List[Candle],
        current_candle: Candle,
        total_score: int,
        price_source: Optional[str] = None,
    ) -> tuple[bool, int]:
        zone = cast(Optional[dict[str, Any]], potential_setup.get("zone")) or {}
        trade_direction = str(potential_setup["trade_direction"])
        signal_context = self._signal_context(potential_setup)
        signal_context["market_price"] = float(current_candle.close)

        signal_factory = SignalFactory()
        atr_value = ATREngine().calculate_atr(recent_candles[-15:], period=14)
        signal = signal_factory.build_signal(
            symbol=current_candle.symbol,
            trade_direction=trade_direction,
            zone_dict=signal_context,
            atr=atr_value,
            score=total_score,
            timestamp=int(current_candle.timestamp),
        )
        trial = is_trial(current_candle.symbol, signal.strategy)
        try:
            signal = dataclass_replace(
                signal, price_source=price_source, code_version=CODE_VERSION, trial=trial
            )
        except TypeError:
            pass

        if repository.is_signal_duplicate(signal.signal_hash):
            logging.info("Skipped duplicate signal: %s", signal.signal_hash)
            return False, 0

        repository.save_signal(signal)
        logging.info("Saved actionable signal: %s", signal.signal_hash)
        try:
            # The stored number gives the card its short code, e.g. #G142.
            signal = dataclass_replace(signal, id=repository.get_signal_id(signal.signal_hash))
        except (TypeError, AttributeError):
            pass

        lifecycle_manager = self.lifecycle_manager_factory(repository)
        target_chat_id = getattr(lifecycle_manager.telegram_client, "chat_id", None)
        if trial:
            # Trial markets and strategies prove themselves in the owner's chat first.
            target_chat_id = admin_chat_id() or None
        elif vip_chat_id():
            target_chat_id = vip_chat_id()
        if not target_chat_id:
            logging.info(
                "Telegram chat id not configured (including UAT routing); skipping signal dispatch for %s",
                signal.signal_hash,
            )
            return True, 0

        chart_png: Optional[bytes] = None
        if CHART_ALERTS_ENABLED:
            try:
                from src.alerting.chart_renderer import ChartRenderer

                chart_png = ChartRenderer().render_signal_chart(
                    candles=recent_candles,
                    signal=signal,
                    zone=zone if zone else None,
                )
            except Exception as exc:
                logging.info("Chart rendering skipped: %s", exc)

        sl_distance_pips = abs(float(signal.entry_price) - float(signal.sl_price))
        try:
            initial_message_id, reasoning_message_id = lifecycle_manager.deploy_signal(
                signal,
                sl_distance_pips=sl_distance_pips,
                chat_id=str(target_chat_id),
                chart_png=chart_png,
            )
            logging.info(
                "Telegram signal dispatched: signal=%s initial_message_id=%s reasoning_message_id=%s",
                signal.signal_hash,
                initial_message_id,
                reasoning_message_id,
            )
        except (TelegramAPIError, ValueError) as exc:
            logging.error(
                "Telegram dispatch failed for signal %s: %s",
                signal.signal_hash,
                exc,
            )
            return True, 1
        if not trial:
            try:
                targets = copy_targets(repository, int(signal.timestamp))
                if targets:
                    send_copies(
                        repository, signal.signal_hash,
                        lifecycle_manager.formatter.format_initial_signal(signal), targets,
                    )
            except Exception as exc:
                logging.error("Signal copies skipped: %s", exc)
        return True, 0

    def _run_macro_regime_check(self, repository: Repository) -> None:
        """Run the macro regime detection, gated behind a 24-hour cache."""
        now = int(time.time())

        # The old made-up central-bank figure must stop acting right away,
        # not at the next daily refresh.
        try:
            if (
                repository.get_kv("macro_cb_net_purchases_source") is None
                and repository.get_kv("macro_long_bias_multiplier") == "1.25"
            ):
                repository.set_kv("macro_long_bias_multiplier", "1.0")
        except Exception as exc:
            logging.debug("Central-bank flag reset skipped: %s", exc)

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
            logging.info("No Gold daily data available for regime detection")
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

        # --- Sovereign Demand Proxy (real hand-entered figure only) ---
        net_purchases = self.sovereign_proxy.get_net_purchases(repository)
        multiplier = self.sovereign_proxy.calculate_multiplier(net_purchases)
        repository.set_kv("macro_long_bias_multiplier", str(multiplier))
        logging.info(
            "Sovereign proxy: net_purchases=%s, long_bias_multiplier=%.2f",
            "unknown" if net_purchases is None else f"{float(net_purchases):.1f}",
            multiplier,
        )

        # --- Crisis Filter (DXY Correlation) ---
        gold_daily_for_dxy = gold_series
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
            logging.info("Insufficient data for crisis filter")

        # --- SMT divergence (gold stretched vs the dollar) ---
        try:
            smt_state, smt_z = self._calculate_smt_state(gold_daily_for_dxy, dxy_series)
            if smt_state is not None:
                repository.set_kv("macro_smt_state", smt_state)
                repository.set_kv("macro_smt_z", f"{smt_z:.2f}")
                logging.info("SMT divergence: z=%.2f state=%s", smt_z, smt_state)
        except Exception as exc:
            logging.error("Failed to update SMT divergence: %s", exc)

        # --- Commitment of Traders (COT) Index ---
        try:
            cot_client = CotClient(repository=repository)
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
        surprise_observations: List[float] = []
        try:
            cal_client = EconomicCalendarClient(repository=repository)
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
                surprise_observations.append(surprise)
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

        # --- Fundamental Shift Rate (FSR) ---
        try:
            if not gold_series.empty:
                fsr_gold = gold_series.tail(FSR_LOOKBACK_PERIOD).tolist()
                if len(fsr_gold) == FSR_LOOKBACK_PERIOD:
                    if len(surprise_observations) < 2:
                        # No calendar surprises available: use a flat surprise
                        # series so FSR still tracks price momentum.
                        aligned_surprise = [0.0] * FSR_LOOKBACK_PERIOD
                    else:
                        aligned_surprise = self._align_series(surprise_observations, FSR_LOOKBACK_PERIOD)
                    fsr_engine = FSREngine()
                    fsr_value = fsr_engine.calculate_fsr(fsr_gold, aligned_surprise)
                    fsr_state = fsr_engine.evaluate_fsr_state(fsr_value)

                    repository.set_kv("macro_fsr_value", f"{fsr_value:.4f}")
                    repository.set_kv("macro_fsr_state", fsr_state)
                    logging.info("FSR Engine: value=%.4f, state=%s", fsr_value, fsr_state)
                else:
                    logging.info("FSR Engine skipped: Not enough gold series data (%d < %d)", len(fsr_gold), FSR_LOOKBACK_PERIOD)
            else:
                logging.warning("FSR Engine skipped: Gold series empty.")
        except Exception as exc:
            logging.error("Failed to update Fundamental Shift Rate: %s", exc)

        # --- Macro-Bias Aggregation (Global Fundamental Bias) ---
        try:
            aggregator = MacroBiasAggregator()
            bias_data = aggregator.calculate_bias(repository)
            
            repository.set_kv("global_macro_score", str(bias_data["score"]))
            repository.set_kv("global_macro_bias", bias_data["bias"])
            
            logging.info(
                "Global Macro Bias: score=%d, bias=%s",
                bias_data["score"],
                bias_data["bias"],
            )
        except Exception as exc:
            logging.error("Failed to update Global Macro Bias: %s", exc)

    @staticmethod
    def _build_forced_setup(current_candle: Candle) -> dict[str, Any]:
        entry_price = round(float(current_candle.close), 2)
        sl_price = round(entry_price - 2.00, 2)
        return {
            "trade_direction": "LONG",
            "strategy": "UAT_FORCE_SIGNAL",
            "order_type": "LIMIT",
            "entry_price": entry_price,
            "sl_price": sl_price,
            "zone": {
                "id": int(current_candle.timestamp),
                "symbol": current_candle.symbol,
                "timeframe": current_candle.timeframe,
                "type": "UAT_OB_BULLISH",
                "status": "ACTIVE",
                "entry_price": entry_price,
                "sl_price": sl_price,
                "strategy": "UAT_FORCE_SIGNAL",
            },
        }

    def _maybe_prune_market_data(self, repository: Repository) -> None:
        """Keep the SQLite file small: prune old candles once per day."""
        now = int(time.time())
        try:
            last_prune = int(repository.get_kv("last_prune_timestamp") or 0)
        except (TypeError, ValueError):
            last_prune = 0
        if now - last_prune < 86400:
            return
        try:
            repository.prune_market_data(MARKET_DATA_RETENTION_DAYS)
            repository.set_kv("last_prune_timestamp", str(now))
            logging.info("Pruned market_data older than %d days", MARKET_DATA_RETENTION_DAYS)
        except Exception as exc:
            logging.debug("Market data prune skipped: %s", exc)
        try:
            repository.prune_health_tables(30)
        except Exception as exc:
            logging.debug("Health history prune skipped: %s", exc)

    # Second attempt at the same level within this bar window scores a bonus
    # (Brooks: "the second signal is more reliable").
    SECOND_ATTEMPT_MIN_BARS = 3
    SECOND_ATTEMPT_MAX_BARS = 20

    @staticmethod
    def _second_attempt_tolerance(symbol: str) -> float:
        instrument = get_instrument(symbol)
        return max(20.0 * instrument.pip_size, 2.0 * instrument.round_buffer)

    def _is_second_attempt(
        self,
        repository: Repository,
        trade_direction: str,
        entry_hint: Optional[float],
        current_candle: Candle,
    ) -> bool:
        if entry_hint is None:
            return False
        raw_symbol = getattr(current_candle, "symbol", None)
        symbol = raw_symbol if isinstance(raw_symbol, str) and raw_symbol else "XAUUSD"
        try:
            raw = repository.get_kv(state_key("last_setup_attempt", symbol))
        except Exception:
            return False
        if not raw or not isinstance(raw, str):
            return False
        try:
            prior = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if not isinstance(prior, dict):
            return False

        try:
            prior_price = float(prior["price"])
            prior_ts = int(prior["timestamp"])
            prior_direction = str(prior["direction"]).upper()
        except (KeyError, TypeError, ValueError):
            return False

        if prior_direction != trade_direction.upper():
            return False

        step = int(TIMEFRAME_SECONDS.get(current_candle.timeframe, 60))
        age_bars = (int(current_candle.timestamp) - prior_ts) / step if step else 0
        if not (self.SECOND_ATTEMPT_MIN_BARS <= age_bars <= self.SECOND_ATTEMPT_MAX_BARS):
            return False

        return abs(float(entry_hint) - prior_price) <= self._second_attempt_tolerance(symbol)

    def _record_setup_attempt(
        self,
        repository: Repository,
        trade_direction: str,
        entry_hint: Optional[float],
        current_candle: Candle,
    ) -> None:
        if entry_hint is None:
            return
        raw_symbol = getattr(current_candle, "symbol", None)
        symbol = raw_symbol if isinstance(raw_symbol, str) and raw_symbol else "XAUUSD"
        try:
            repository.set_kv(
                state_key("last_setup_attempt", symbol),
                json.dumps(
                    {
                        "price": float(entry_hint),
                        "direction": trade_direction.upper(),
                        "timestamp": int(current_candle.timestamp),
                    }
                ),
            )
        except Exception as exc:
            logging.debug("Setup attempt record skipped: %s", exc)

    def _maybe_refresh_news_calendar(self, repository: Repository) -> None:
        """Hands-free news blackout: sync high-impact USD events twice a day."""
        if not NEWS_AUTOFETCH_ENABLED:
            return
        now = int(time.time())
        try:
            last_refresh = int(repository.get_kv("last_news_calendar_refresh") or 0)
        except (TypeError, ValueError):
            last_refresh = 0
        if now - last_refresh < NEWS_CALENDAR_REFRESH_HOURS * 3600:
            return
        try:
            from src.ingestion.news_calendar import refresh_news_blackouts

            refresh_news_blackouts(repository, now)
            repository.set_kv("last_news_calendar_refresh", str(now))
        except Exception as exc:
            logging.warning("News calendar refresh failed (will retry): %s", exc)

    def _maybe_send_weekly_report(self, repository: Repository) -> None:
        """Hands-free calibration: post the performance report to Telegram weekly."""
        if not WEEKLY_REPORT_ENABLED:
            return
        now = int(time.time())
        try:
            last_sent = int(repository.get_kv("weekly_report_last_sent") or 0)
        except (TypeError, ValueError):
            last_sent = 0
        if now - last_sent < WEEKLY_REPORT_INTERVAL_DAYS * 86400:
            return
        try:
            from config.settings import DB_PATH
            from scripts.calibrate_from_history import analyze
            from src.alerting.weekly_report import build_weekly_report

            analysis = analyze(str(DB_PATH))
            total_trades = sum(
                int(stats.get("trades", 0))
                for stats in analysis.get("strategies", {}).values()
            )
            if total_trades < WEEKLY_REPORT_MIN_TRADES:
                return

            message = build_weekly_report(analysis)
            # Tuning advice is for the owner; subscribers get the results card.
            notify_admin(
                message, repository=repository, telegram_client=self.telegram_client_factory()
            )
            repository.set_kv("weekly_report_last_sent", str(now))
            logging.info("Weekly performance report sent (%d trades)", total_trades)
        except Exception as exc:
            logging.warning("Weekly report skipped (will retry): %s", exc)

    def _record_pulse_health(
        self,
        repository: Optional[Repository],
        errors_encountered: int,
    ) -> None:
        """Heartbeat + sick-bot alarm: after 5 consecutive failing pulses,
        send one admin alert (6h cooldown) so silent death is impossible."""
        if repository is None:
            return
        try:
            now = int(time.time())
            repository.set_kv("last_pulse_wallclock", str(now))
            if errors_encountered == 0:
                repository.set_kv("consecutive_pulse_errors", "0")
                return

            try:
                streak = int(repository.get_kv("consecutive_pulse_errors") or 0) + 1
            except (TypeError, ValueError):
                streak = 1
            repository.set_kv("consecutive_pulse_errors", str(streak))

            if streak < 5:
                return
            try:
                last_alert = int(repository.get_kv("last_error_alert_ts") or 0)
            except (TypeError, ValueError):
                last_alert = 0
            if now - last_alert < 6 * 3600:
                return

            notify_admin(
                "🩺 <b>Bot health alert</b>\n"
                f"{streak} consecutive pulses hit errors. "
                "Check the Actions log / data feed. Signals may be delayed.",
                repository=repository,
                telegram_client=self.telegram_client_factory(),
            )
            repository.set_kv("last_error_alert_ts", str(now))
        except Exception as exc:
            logging.debug("Pulse health recording skipped: %s", exc)

    def _log_blocked_setup(
        self,
        repository: Repository,
        symbol: str,
        setup: dict[str, Any],
        current_candle: Candle,
        reason: str,
        levels: Optional[tuple[float, float, float, float]] = None,
    ) -> None:
        """Funnel visibility for pre-scoring blocks (macro gates, governor):
        a benched market must look benched, not broken."""
        try:
            repository.log_setup(
                symbol=symbol,
                strategy=str(setup.get("strategy") or "SMC_ZONE"),
                direction=str(setup.get("trade_direction", "")),
                order_type=str(setup.get("order_type", "LIMIT")).upper(),
                score=0,
                classification="BLOCKED",
                vetoes=str(reason),
                timestamp=int(current_candle.timestamp),
                levels=levels,
            )
        except Exception as exc:
            logging.debug("Blocked-setup funnel logging skipped: %s", exc)

    def _load_auto_quarantine(self, repository: Repository) -> None:
        """Refresh the in-memory set of live-quarantined strategies."""
        self._extra_disabled = set()
        try:
            raw = repository.get_kv("auto_quarantined_strategies")
            if isinstance(raw, str) and raw:
                payload = json.loads(raw)
                if isinstance(payload, list):
                    self._extra_disabled = {str(name).upper() for name in payload}
        except Exception as exc:
            logging.debug("Auto-quarantine load skipped: %s", exc)

    def _maybe_update_strategy_quarantine(self, repository: Repository) -> None:
        """Self-coaching loop: once a day, quarantine any strategy whose LIVE
        expectancy over the last 45 days is clearly negative (>=8 closed
        trades, <= -0.25R/trade). Mirrors the manual Quasimodo decision."""
        if not AUTO_QUARANTINE_ENABLED:
            return
        now = int(time.time())
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        try:
            if repository.get_kv("strategy_quarantine_last_date") == today:
                return
        except Exception:
            return
        try:
            outcomes = repository.get_closed_results_since(now - 45 * 86400)
            if not isinstance(outcomes, list):
                return
            stats: dict[str, list[float]] = {}
            for strategy, _symbol, r_value in outcomes:
                stats.setdefault(str(strategy).upper(), []).append(float(r_value))

            existing = set(self._extra_disabled)
            newly = []
            for strategy, r_values in stats.items():
                if len(r_values) < 8 or strategy in existing or strategy in DISABLED_STRATEGIES:
                    continue
                expectancy = sum(r_values) / len(r_values)
                if expectancy <= -0.25:
                    newly.append((strategy, expectancy, len(r_values)))

            if newly:
                updated = sorted(existing | {name for name, _, _ in newly})
                repository.set_kv("auto_quarantined_strategies", json.dumps(updated))
                self._extra_disabled = set(updated)
                lines = "\n".join(
                    f"\u2022 {name}: {exp:+.2f}R/trade over {n} live trades"
                    for name, exp, n in newly
                )
                notify_admin(
                    "\U0001f9ea <b>Strategy auto-quarantine</b>\n"
                    f"{lines}\n"
                    "<i>Suspended based on live results; clear "
                    "auto_quarantined_strategies in the DB to re-enable.</i>",
                    repository=repository,
                    telegram_client=self.telegram_client_factory(),
                )
                logging.info("Auto-quarantined strategies: %s", [n for n, _, _ in newly])
            repository.set_kv("strategy_quarantine_last_date", today)
        except Exception as exc:
            logging.debug("Strategy quarantine check skipped: %s", exc)

    def _maybe_check_live_vs_history(self, repository: Repository) -> None:
        """Weekly: warn the owner when a strategy's live results fall clearly
        below what years of history predict (it may be wearing out). Live
        results are before costs and history after costs, so only a clear drop
        is flagged."""
        now = int(time.time())
        try:
            if now - int(repository.get_kv("history_drift_check_at") or 0) < 7 * 86400:
                return
        except (TypeError, ValueError):
            pass
        try:
            baselines = load_evidence().get("baselines") or {}
            if not baselines:
                return
            repository.set_kv("history_drift_check_at", str(now))
            groups: dict[str, list[float]] = {}
            for strategy, symbol, r_value in repository.get_closed_results_since(now - 60 * 86400):
                groups.setdefault(f"{str(symbol).upper()}|{str(strategy).upper()}", []).append(float(r_value))
            flagged = []
            for key, values in groups.items():
                symbol, strategy = key.split("|", 1)
                base = evidence_baseline(symbol, strategy)
                if len(values) < 10 or not base or int(base.get("trades") or 0) < 30:
                    continue
                live_mean = sum(values) / len(values)
                floor = float(base.get("expectancy_r") or 0.0) - 2.0 * float(base.get("std_r") or 0.0) / (len(values) ** 0.5)
                if live_mean < floor:
                    flagged.append(
                        f"\u2022 {strategy} on {symbol}: {live_mean:+.2f}R per trade live over {len(values)} "
                        f"trades vs {float(base['expectancy_r']):+.2f}R in history"
                    )
            if flagged:
                notify_admin(
                    "\U0001f4c9 <b>Live results below history</b>\n" + "\n".join(flagged)
                    + "\n<i>Not switched off automatically; the monthly history proof decides.</i>",
                    repository=repository,
                    telegram_client=self.telegram_client_factory(),
                )
        except Exception as exc:
            logging.debug("Live-vs-history check skipped: %s", exc)

    def _public_post(self, repository: Repository, text: str, kind: str) -> bool:
        """A message for the public channel (outbox-protected)."""
        client = self.telegram_client_factory()
        chat = getattr(client, "chat_id", None)
        if not chat:
            return False
        try:
            deliver(client, repository, text, chat_id=str(chat), kind=kind)
            return True
        except (TelegramAPIError, ValueError) as exc:
            logging.error("Public %s post not delivered (queued): %s", kind, exc)
            return False

    def _maybe_post_morning_briefing(self, repository: Repository) -> None:
        """Weekdays, first run after MORNING_BRIEFING_HOUR_UTC: key gold
        levels, today's big news in Ethiopian time and the day's leaning."""
        if not app_settings.MORNING_BRIEFING_ENABLED:
            return
        now = int(time.time())
        moment = time.gmtime(now)
        if moment.tm_wday >= 5 or moment.tm_hour < int(app_settings.MORNING_BRIEFING_HOUR_UTC):
            return
        today = time.strftime("%Y-%m-%d", moment)
        try:
            if repository.get_kv("morning_briefing_date") == today:
                return
            repository.set_kv("morning_briefing_date", today)
            from src.alerting.public_posts import build_morning_briefing

            self._public_post(repository, build_morning_briefing(repository, now), "briefing")
        except Exception as exc:
            logging.debug("Morning briefing skipped: %s", exc)

    def _maybe_post_news_pauses(self, repository: Repository) -> None:
        """One public notice when a news pause starts (events at the same
        minute share one notice)."""
        if not app_settings.PAUSE_NOTICES_ENABLED:
            return
        try:
            from src.alerting.public_posts import _events, _public_currencies, news_pause_notice
            from src.analysis.risk_governor import event_currency

            now = int(time.time())
            currencies = _public_currencies()
            due: dict[int, list[dict[str, Any]]] = {}
            for event in _events(repository):
                event_ts = int(event.get("timestamp", 0) or 0)
                if 0 < event_ts - now <= NEWS_BLACKOUT_BEFORE_MIN * 60 and event_currency(event) in currencies:
                    due.setdefault(event_ts, []).append(event)
            for event_ts, events in sorted(due.items()):
                key = f"news_pause_posted:{event_ts}"
                if repository.get_kv(key):
                    continue
                repository.set_kv(key, str(now))
                merged = dict(events[0])
                merged["label"] = " + ".join(str(e.get("label") or "news") for e in events[:3])
                self._public_post(
                    repository,
                    news_pause_notice(merged, NEWS_BLACKOUT_BEFORE_MIN, NEWS_BLACKOUT_AFTER_MIN),
                    "pause",
                )
        except Exception as exc:
            logging.debug("News pause notice skipped: %s", exc)

    def _maybe_post_brake_notice(
        self, repository: Repository, reason: str, symbol: str, setup: dict[str, Any]
    ) -> None:
        """Tell subscribers once why signals stopped (daily/weekly loss limit, owner pause)."""
        if not app_settings.PAUSE_NOTICES_ENABLED or is_trial(symbol, setup.get("strategy")):
            return
        try:
            from src.alerting.public_posts import brake_notice

            found = brake_notice(reason)
            if found is None:
                return
            marker, text = found
            period = time.strftime("%G-W%V" if "weekly" in marker else "%Y-%m-%d", time.gmtime())
            key = f"brake_notice:{marker}:{period}"
            if repository.get_kv(key):
                return
            repository.set_kv(key, str(int(time.time())))
            self._public_post(repository, text, "pause")
        except Exception as exc:
            logging.debug("Brake notice skipped: %s", exc)

    def _maybe_post_weekly_card(self, repository: Repository) -> None:
        """Saturday morning: the week's results as an image, losses included."""
        if not app_settings.WEEKLY_CARD_ENABLED:
            return
        now = int(time.time())
        moment = time.gmtime(now)
        if moment.tm_wday != 5 or moment.tm_hour < 6:
            return
        try:
            from src.alerting.public_posts import (
                render_weekly_card,
                results_week_start,
                weekly_caption,
                weekly_results,
            )

            week_start = results_week_start(now)
            if repository.get_kv("weekly_card_week") == str(week_start):
                return
            repository.set_kv("weekly_card_week", str(week_start))
            stats = weekly_results(repository, week_start)
            caption = weekly_caption(stats)
            client = self.telegram_client_factory()
            if not getattr(client, "chat_id", None):
                return
            if stats["trades"]:
                client.send_photo(render_weekly_card(stats), caption=caption)
            else:
                deliver(client, repository, caption, chat_id=str(client.chat_id), kind="weekly")
        except Exception as exc:
            logging.error("Weekly results card not posted: %s", exc)

    def _maybe_post_weekly_lesson(self, repository: Repository) -> None:
        """Sunday evening (Ethiopia): one short lesson on risk and patience."""
        if not app_settings.WEEKLY_LESSON_ENABLED:
            return
        now = int(time.time())
        moment = time.gmtime(now)
        if moment.tm_wday != 6 or moment.tm_hour < 16:
            return
        week = time.strftime("%G-W%V", moment)
        try:
            if repository.get_kv("weekly_lesson_week") == week:
                return
            repository.set_kv("weekly_lesson_week", week)
            from src.alerting.i18n import t
            from src.alerting.public_posts import lesson_for_week

            iso_week = int(time.strftime("%V", moment))
            self._public_post(
                repository, f"\U0001f4d8 <b>{t('lesson')}</b>\n{lesson_for_week(iso_week)}", "lesson"
            )
        except Exception as exc:
            logging.debug("Weekly lesson skipped: %s", exc)

    def _maybe_send_monthly_risk_review(self, repository: Repository) -> None:
        """First run of each month: last month's worst day, worst losing
        streak and biggest loss against the plan, in the owner's chat."""
        if not app_settings.MONTHLY_RISK_REVIEW_ENABLED:
            return
        moment = datetime.now(timezone.utc)
        year, month = (moment.year, moment.month - 1) if moment.month > 1 else (moment.year - 1, 12)
        label = f"{year:04d}-{month:02d}"
        try:
            if repository.get_kv("monthly_risk_review_month") == label:
                return
            from src.alerting.owner_reports import build_monthly_risk_review

            message = build_monthly_risk_review(repository, year, month)
            repository.set_kv("monthly_risk_review_month", label)
            if message:
                notify_admin(
                    message, repository=repository, telegram_client=self.telegram_client_factory()
                )
        except Exception as exc:
            logging.debug("Monthly risk review skipped: %s", exc)

    def _reply_once(
        self, repository: Repository, signal: Any, key: str, text: str
    ) -> None:
        """Threaded reply under a signal card, sent at most once per key."""
        if repository.get_kv(key):
            return
        try:
            message_id = int(repository.get_signal_message_id(signal.signal_hash))
        except (KeyError, TypeError, ValueError):
            return
        repository.set_kv(key, str(int(time.time())))
        client = self.telegram_client_factory()
        try:
            deliver(
                client, repository, text,
                chat_id=getattr(signal, "telegram_chat_id", None),
                reply_to_message_id=message_id, kind="reply", signal_hash=signal.signal_hash,
            )
        except (TelegramAPIError, ValueError) as exc:
            logging.error("Trade notice not delivered (queued): %s", exc)

    def _notify_open_trades(self, repository: Repository, symbol: str, current_candle: Candle) -> None:
        """Warn holders of this market's open trades before high-impact news
        for its currencies, and tell them the weekend plan when it applies."""
        try:
            open_signals = [
                s for s in repository.get_open_signals()
                if str(s.symbol).upper() == symbol.upper() and str(s.status).upper() in {"ACTIVE", "PARTIAL_TP1"}
            ]
        except Exception as exc:
            logging.debug("Open-trade notices skipped: %s", exc)
            return
        if not open_signals:
            return
        from src.alerting.timefmt import eat_time
        from src.analysis.risk_governor import KV_NEWS_EVENTS, event_currency

        now = int(time.time())
        warn_seconds = int(app_settings.NEWS_WARN_MINUTES) * 60
        events: list[dict[str, Any]] = []
        if warn_seconds > 0:
            try:
                raw = repository.get_kv(KV_NEWS_EVENTS)
                parsed = json.loads(raw) if isinstance(raw, str) and raw else []
                currencies = set(get_instrument(symbol).news_currencies)
                events = [
                    e for e in parsed if isinstance(e, dict)
                    and event_currency(e) in currencies
                    and 0 < int(e.get("timestamp", 0)) - now <= warn_seconds
                ]
            except (TypeError, ValueError):
                events = []
        nd = get_instrument(symbol).price_decimals
        for signal in open_signals:
            for event in events:
                event_ts = int(event["timestamp"])
                minutes = max(1, (event_ts - now) // 60)
                self._reply_once(
                    repository, signal, f"news_warned:{signal.signal_hash}:{event_ts}",
                    f"\u26a0\ufe0f <b>Big news in {minutes} min</b> ({eat_time(event_ts)}): "
                    f"{html.escape(str(event.get('label') or 'high-impact news'))} "
                    f"({event_currency(event)}).\nThis trade is still open. Prices can jump: "
                    f"consider closing it or moving the stop to entry (<code>{float(signal.entry_price):.{nd}f}</code>).",
                )
            if (
                str(app_settings.WEEKEND_ACTION).lower() == "breakeven"
                and str(signal.status).upper() == "ACTIVE"
                and SignalLifecycleManager._held_into_weekend(signal, current_candle)
            ):
                self._reply_once(
                    repository, signal, f"weekend_be:{signal.signal_hash}",
                    "\U0001f6e1\ufe0f <b>Weekend plan</b>: move your stop to entry "
                    f"(<code>{float(signal.entry_price):.{nd}f}</code>). The trade stays open "
                    "over the weekend with no risk left at the normal price.",
                )

    def _maybe_send_daily_status(self, repository: Repository) -> None:
        """The owner's daily check: how many runs happened, which price feeds
        were used and how late, signals sent, and any warning signs."""
        if not DAILY_STATUS_ENABLED:
            return
        now = int(time.time())
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        if time.gmtime(now).tm_hour < DAILY_STATUS_HOUR_UTC:
            return
        try:
            if repository.get_kv("daily_status_last_date") == today:
                return
        except Exception:
            return
        try:
            from src.alerting.owner_reports import build_daily_check

            message = build_daily_check(repository, now)

            # Regime-drift watch: negative rolling 14-day expectancy is flagged
            # before it can quietly bleed the account.
            try:
                recent = repository.get_closed_results_since(now - 14 * 86400)
                r_values = [float(r) for _, _, r in recent]
                if len(r_values) >= 6 and sum(r_values) <= -3.0:
                    message += (
                        f"\n\n\u26a0\ufe0f 14-day results: {sum(r_values):+.1f}R over "
                        f"{len(r_values)} trades \u2014 conditions may have changed; review the dashboard."
                    )
            except Exception as exc:
                logging.debug("Drift line skipped: %s", exc)

            notify_admin(
                message, repository=repository, telegram_client=self.telegram_client_factory()
            )
            repository.set_kv("daily_status_last_date", today)
        except Exception as exc:
            logging.debug("Daily status skipped: %s", exc)

    @staticmethod
    def _price_source(client: Any, symbol: str) -> Optional[str]:
        sources = getattr(client, "last_source", None)
        if isinstance(sources, dict):
            value = sources.get(symbol)
            return str(value) if value else None
        return None

    def _adopt_spot_feed(
        self,
        repository: Repository,
        client: Any,
        symbol: str,
        timeframe: str,
    ) -> List[Candle]:
        """First successful spot fetch: history, zones and swing state drawn on
        the old futures prices are dropped and refilled from the spot feed.
        Open signals priced on the old feed are withdrawn."""
        now = int(time.time())
        repository.purge_symbol_price_history(symbol)
        for base in PRICE_STATE_KEYS:
            repository.delete_kv(state_key(base, symbol))
        refill_from = str(now - 7 * 86400)
        repository.set_kv(f"last_processed_{symbol}", refill_from)
        if symbol == "XAUUSD":
            repository.set_kv("last_processed_timestamp", refill_from)

        withdrawn = 0
        reason = (
            "Withdrawn: this market now uses broker-style spot prices; this "
            "signal's levels came from the old futures feed. Close or cancel it."
        )
        for signal in repository.get_open_signals():
            if str(getattr(signal, "symbol", "")).upper() != symbol.upper():
                continue
            repository.update_signal_closure(signal.signal_hash, reason, "CANCELLED")
            withdrawn += 1
            message_id = getattr(signal, "telegram_message_id", None)
            chat_id = getattr(signal, "telegram_chat_id", None)
            if message_id and chat_id:
                try:
                    deliver(
                        self.telegram_client_factory(), repository,
                        "\u26a0\ufe0f <b>Signal withdrawn</b>\n" + reason,
                        chat_id=str(chat_id), reply_to_message_id=int(message_id),
                        kind="reply", signal_hash=signal.signal_hash,
                    )
                except (TelegramAPIError, ValueError) as exc:
                    logging.error("Withdrawal notice not delivered: %s", exc)

        repository.set_kv(feed_kind_key(symbol), SOURCE_SPOT)
        candles = client.fetch_latest_candles(symbol, timeframe)
        notify_admin(
            f"\U0001f504 <b>{get_instrument(symbol).display_name} now uses spot prices</b>\n"
            f"Old futures history and zones were cleared; {len(candles)} spot candles loaded."
            + (f" {withdrawn} open signal(s) withdrawn." if withdrawn else ""),
            repository=repository,
            telegram_client=self.telegram_client_factory(),
        )
        logging.warning("%s switched to the spot feed (%d candles loaded)", symbol, len(candles))
        return candles

    def _update_shadow_outcomes(
        self,
        repository: Repository,
        symbol: str,
        new_candles: List[Candle],
    ) -> None:
        """Follow every blocked/rejected idea with the live trade rules to see
        what it would have done (the evidence for judging each filter)."""
        if not SHADOW_TRACKING_ENABLED or not new_candles:
            return
        try:
            rows = repository.get_open_shadow_setups(symbol)
        except Exception:
            return
        if not isinstance(rows, list) or not rows:
            return

        judge = SignalLifecycleManager(telegram_client=cast(Any, None))
        max_age = int(SHADOW_MAX_AGE_DAYS) * 86400
        changed: dict[int, dict[str, Any]] = {}
        for candle in sorted(new_candles, key=lambda item: item.timestamp):
            for row in rows:
                if row.get("closed") or int(candle.timestamp) <= int(row["timestamp"]):
                    continue
                event = judge.evaluate_signal(row, candle)
                if event is None and row["status"] == "PARTIAL_TP1":
                    event = SignalLifecycleManager._structure_exit_event(repository, row)
                if event is None and int(candle.timestamp) - int(row["timestamp"]) > max_age:
                    event = "STRUCTURE_EXIT" if row["status"] == "PARTIAL_TP1" else (
                        "TIME_STOP" if row["status"] == "ACTIVE" else "EXPIRED"
                    )
                if event is None:
                    if row["status"] in {"ACTIVE", "PARTIAL_TP1"}:
                        risk = abs(float(row["entry_price"]) - float(row["sl_price"]))
                        if risk > 0:
                            favorable = (
                                float(candle.high) - float(row["entry_price"])
                                if str(row["signal_type"]).upper() == "LONG"
                                else float(row["entry_price"]) - float(candle.low)
                            )
                            row["mfe_r"] = max(float(row.get("mfe_r") or 0.0), favorable / risk)
                            changed[int(row["id"])] = row
                    continue

                new_status = SignalLifecycleManager.EVENT_STATUS_MAP[event]
                row["status"] = new_status
                if new_status.startswith("CLOSED") or new_status == "CANCELLED":
                    row["closed"] = True
                    row["realized_r"] = (
                        None
                        if new_status == "CANCELLED"
                        else realized_r_for_event(
                            event,
                            str(row["signal_type"]),
                            float(row["entry_price"]),
                            float(row["sl_price"]),
                            float(row["tp2_price"]),
                            exit_price=float(candle.close),
                        )
                    )
                    row["closed_at"] = int(candle.timestamp)
                changed[int(row["id"])] = row

        for row_id, row in changed.items():
            try:
                repository.update_shadow_setup(
                    row_id,
                    str(row["status"]),
                    float(row.get("mfe_r") or 0.0),
                    realized_r=row.get("realized_r"),
                    closed_at=row.get("closed_at"),
                )
            except Exception as exc:
                logging.debug("Shadow outcome update skipped: %s", exc)

    def _record_feed_health(
        self,
        repository: Repository,
        symbol: str,
        timeframe: str,
        source: Optional[str],
        newest_ts: Optional[int],
    ) -> None:
        """How late the newest candle is; the owner hears when a feed falls behind."""
        from src.analysis.market_hours import market_open

        now = int(time.time())
        step = int(TIMEFRAME_SECONDS.get(timeframe, 300))
        lag = None if not newest_ts else max(0, now - (int(newest_ts) + step))
        label = source or "MOCK"
        self._run_feed[symbol] = {
            "source": label,
            "lag_min": None if lag is None else round(lag / 60.0, 1),
        }
        if not OPS_TELEMETRY_ENABLED:
            return
        try:
            repository.record_feed_health(symbol, label, lag, lag is not None, now)
        except Exception as exc:
            logging.debug("Feed health record skipped: %s", exc)
            return
        if (
            lag is None
            or lag < FEED_LAG_ALERT_MINUTES * 60 + max(0, step - 300)
            or not market_open(symbol, now)
        ):
            return
        alert_key = f"feed_lag_alert_at:{symbol}"
        try:
            if now - int(repository.get_kv(alert_key) or 0) < 2 * 3600:
                return
        except (TypeError, ValueError):
            pass
        repository.set_kv(alert_key, str(now))
        notify_admin(
            f"⏰ <b>{get_instrument(symbol).display_name} prices are {lag // 60} minutes late</b>\n"
            f"Source: {label}. Signals for this market wait until fresh prices arrive.",
            repository=repository,
            telegram_client=self.telegram_client_factory(),
        )

    def _pulse_symbol(
        self,
        repository: Repository,
        client: Any,
        validator: DataValidator,
        symbol: str,
        timeframe: str,
        force_signal: bool = False,
    ) -> tuple[int, int]:
        """Full detection pipeline for one instrument.
        Returns (signals_generated, errors_encountered)."""
        signals_generated = 0
        errors_encountered = 0

        try:
            candles = client.fetch_latest_candles(symbol, timeframe)
        except (YahooDataIngestionError, TwelveDataIngestionError) as exc:
            logging.error("Ingestion failed for %s: %s", symbol, exc)
            return signals_generated, errors_encountered + 1

        price_source = self._price_source(client, symbol)
        if price_source == SOURCE_SPOT and repository.get_kv(feed_kind_key(symbol)) != SOURCE_SPOT:
            try:
                candles = self._adopt_spot_feed(repository, client, symbol, timeframe)
            except (YahooDataIngestionError, TwelveDataIngestionError) as exc:
                logging.error("Spot history load failed for %s: %s", symbol, exc)
                return signals_generated, errors_encountered + 1

        self.memory_profiler.log_snapshot(f"Post-ingestion {symbol}")

        total_count = len(candles)
        valid_candles = validator.filter_candles(candles)
        del candles
        filtered = total_count - len(valid_candles)
        logging.info(
            "%s candles received: %s, valid: %s", symbol, total_count, len(valid_candles)
        )
        if filtered:
            logging.info("Filtered out %s invalid %s candles", filtered, symbol)

        if valid_candles:
            newest_ts: Optional[int] = max(candle.timestamp for candle in valid_candles)
        else:
            try:
                newest_ts = int(repository.get_kv(f"last_processed_{symbol}") or 0) or None
            except (TypeError, ValueError):
                newest_ts = None
        self._record_feed_health(repository, symbol, timeframe, price_source, newest_ts)

        if not valid_candles:
            logging.info("No valid candles for %s; skipping symbol", symbol)
            return signals_generated, errors_encountered

        repository.save_candles(valid_candles)
        latest_timestamp = max(candle.timestamp for candle in valid_candles)
        repository.set_kv(f"last_processed_{symbol}", latest_timestamp)
        if symbol == "XAUUSD" or os.getenv("MOCK_INGESTION") == "1":
            # Legacy global watermark (heartbeats, mock clock, old dashboards).
            repository.set_kv("last_processed_timestamp", latest_timestamp)
        current_candle = max(valid_candles, key=lambda candle: candle.timestamp)
        errors_encountered += self._monitor_open_signals(repository, valid_candles)
        self._notify_open_trades(repository, symbol, current_candle)
        self._update_shadow_outcomes(repository, symbol, valid_candles)
        self._evaluate_zone_lifecycle(repository, symbol, valid_candles)

        detector = FractalDetector()
        recent_candles = repository.get_recent_candles(
            symbol, timeframe, ANALYSIS_LOOKBACK_CANDLES
        )
        latest_fractals = detector.find_fractals(recent_candles)
        self._persist_latest_fractals(repository, latest_fractals, symbol)
        self._evaluate_liquidity_sweep(repository, recent_candles, valid_candles)
        prev_close = (
            float(recent_candles[-2].close)
            if isinstance(recent_candles, list) and len(recent_candles) >= 2
            else None
        )
        recent_bos_type = self._evaluate_market_structure(
            repository, current_candle, prev_close
        )
        recent_fvgs = self._scan_for_fvg_zones(repository, recent_candles)
        self._scan_for_order_blocks(
            repository,
            recent_candles,
            recent_bos_type,
            recent_fvgs,
        )

        if force_signal:
            logging.info("UAT force-signal enabled; bypassing permission and scoring engines")
            forced_setup = self._build_forced_setup(current_candle)
            try:
                signal_saved, signal_errors = self._persist_actionable_signal(
                    repository,
                    forced_setup,
                    recent_candles,
                    current_candle,
                    100,
                )
                if signal_saved:
                    signals_generated += 1
                errors_encountered += signal_errors
            except Exception as exc:
                errors_encountered += 1
                logging.error("Failed to persist forced UAT signal: %s", exc)
            return signals_generated, errors_encountered

        if not get_instrument(symbol).signals_enabled:
            # Watch-only market: data, zones and monitoring stay live, but no
            # new signals are published (replay evidence gate).
            del recent_fvgs
            del recent_candles
            del valid_candles
            return signals_generated, errors_encountered

        potential_setup = self._detect_trade_setup(
            repository,
            symbol,
            current_candle,
            recent_candles,
            new_candle_count=len(valid_candles),
        )
        if potential_setup is not None:
            if quiet_hour(symbol, int(current_candle.timestamp)):
                self._log_blocked_setup(
                    repository, symbol, potential_setup, current_candle,
                    "Quiet hour: history tests show this hour loses for this market",
                    levels=self._hypothetical_levels(potential_setup, recent_candles, symbol),
                )
                return signals_generated, errors_encountered

            if str(app_settings.TREND_DAY_FILTER).lower() == "block":
                one_way = against_trend_day(
                    recent_candles, str(potential_setup.get("trade_direction", ""))
                )
                if one_way:
                    self._log_blocked_setup(
                        repository, symbol, potential_setup, current_candle, one_way,
                        levels=self._hypothetical_levels(potential_setup, recent_candles, symbol),
                    )
                    return signals_generated, errors_encountered

            permission_engine = PermissionEngine()
            macro_context = self._build_macro_permission_context(repository)
            is_permitted, permission_reason = permission_engine.is_trade_permitted(
                cast(dict[str, Any], potential_setup),
                macro_context,
                symbol=symbol,
            )
            if not is_permitted:
                logging.info(
                    "Setup blocked by permission filter: symbol=%s direction=%s reason=%s",
                    symbol,
                    potential_setup.get("trade_direction"),
                    permission_reason,
                )
                self._log_blocked_setup(
                    repository, symbol, potential_setup, current_candle, permission_reason,
                    levels=self._hypothetical_levels(potential_setup, recent_candles, symbol),
                )
                return signals_generated, errors_encountered

            governor = RiskGovernor()
            trading_allowed, governor_reason = governor.is_trading_allowed(
                repository,
                int(current_candle.timestamp),
                symbol=symbol,
                direction=str(potential_setup.get("trade_direction", "")),
                trial=is_trial(symbol, potential_setup.get("strategy")),
            )
            if not trading_allowed:
                logging.info("Setup blocked: %s", governor_reason)
                self._log_blocked_setup(
                    repository, symbol, potential_setup, current_candle, governor_reason,
                    levels=self._hypothetical_levels(potential_setup, recent_candles, symbol),
                )
                self._maybe_post_brake_notice(repository, governor_reason, symbol, potential_setup)
                return signals_generated, errors_encountered

            raw_macro_bias_state = repository.get_kv("macro_bias_state")
            if raw_macro_bias_state is None:
                raw_macro_bias_state = repository.get_kv("global_macro_bias")
            macro_bias_state = (
                raw_macro_bias_state.upper() if raw_macro_bias_state is not None else "NEUTRAL"
            )
            if not get_instrument(symbol).macro_gold_filters:
                # Gold's macro regime must not vote on other markets.
                macro_bias_state = "NEUTRAL"

            raw_structure_state = repository.get_kv(
                state_key("current_structure_state", symbol)
            )
            current_structure_state = (
                raw_structure_state.upper() if raw_structure_state is not None else "BULLISH"
            )

            latest_sweep = self._parse_liquidity_sweep(
                repository.get_kv(state_key("latest_liquidity_sweep", symbol))
            )
            has_recent_sweep = self._has_recent_directional_sweep(
                trade_direction=str(potential_setup["trade_direction"]),
                current_timestamp=int(current_candle.timestamp),
                timeframe=current_candle.timeframe,
                latest_sweep=latest_sweep,
            )

            trade_direction = str(potential_setup["trade_direction"])
            setup_order_type = str(potential_setup.get("order_type", "LIMIT")).upper()
            setup_strategy = potential_setup.get("strategy")
            zone_for_scoring = cast(Optional[dict[str, Any]], potential_setup.get("zone"))

            entry_hint: Optional[float] = None
            raw_entry = potential_setup.get("entry_price")
            if raw_entry is None and zone_for_scoring:
                key = "price_top" if trade_direction.upper() == "LONG" else "price_bottom"
                raw_entry = zone_for_scoring.get(key)
            try:
                entry_hint = float(raw_entry) if raw_entry is not None else None
            except (TypeError, ValueError):
                entry_hint = None

            swing_high_point = self._parse_swing_point(
                repository.get_kv(state_key("last_swing_high", symbol))
            )
            swing_low_point = self._parse_swing_point(
                repository.get_kv(state_key("last_swing_low", symbol))
            )
            swing_high_price = (
                float(swing_high_point["price"]) if swing_high_point else None
            )
            swing_low_price = (
                float(swing_low_point["price"]) if swing_low_point else None
            )

            second_attempt = self._is_second_attempt(
                repository, trade_direction, entry_hint, current_candle
            )

            swing_history: Optional[dict[str, Any]] = None
            raw_swing_history = repository.get_kv(state_key("swing_history", symbol))
            if isinstance(raw_swing_history, str):
                try:
                    parsed_history = json.loads(raw_swing_history)
                    if isinstance(parsed_history, dict):
                        swing_history = parsed_history
                except (TypeError, ValueError):
                    swing_history = None

            vetoes_text = ""
            try:
                confluence = ConfluenceEngineV2().evaluate(
                    trade_direction=trade_direction,
                    macro_bias=macro_bias_state,
                    current_structure=current_structure_state,
                    zone_dict=zone_for_scoring,
                    has_recent_sweep=has_recent_sweep,
                    recent_candles=recent_candles,
                    current_timestamp=int(current_candle.timestamp),
                    order_type=setup_order_type,
                    strategy=str(setup_strategy) if setup_strategy else None,
                    repository=repository,
                    entry_price=entry_hint,
                    last_swing_high=swing_high_price,
                    last_swing_low=swing_low_price,
                    second_attempt=second_attempt,
                    swing_history=swing_history,
                    symbol=symbol,
                )
                total_score = int(confluence["score"])
                classification = str(confluence["classification"])
                if confluence.get("vetoes"):
                    vetoes_text = "; ".join(str(v) for v in confluence["vetoes"])
                    logging.info("Confluence vetoes applied: %s", vetoes_text)
                potential_setup["confluence_notes"] = list(confluence.get("notes", []))
            except Exception as exc:
                logging.error("Confluence v2 failed; using legacy scoring: %s", exc)
                scoring_engine = ScoringEngine()
                total_score = scoring_engine.calculate_total_score(
                    trade_direction=trade_direction,
                    macro_bias=macro_bias_state,
                    current_structure=current_structure_state,
                    zone_dict=zone_for_scoring,
                    has_recent_sweep=has_recent_sweep,
                )
                classification = scoring_engine.classify_score(total_score)

            penalties = permission_engine.score_penalties(
                cast(dict[str, Any], potential_setup), macro_context, symbol=symbol
            )
            if penalties:
                total_score = max(0, int(total_score) - sum(points for points, _ in penalties))
                penalty_notes = [note for _, note in penalties]
                potential_setup["confluence_notes"] = (
                    list(potential_setup.get("confluence_notes", [])) + penalty_notes
                )
                if classification != "REJECTED":
                    classification = ScoringEngine().classify_score(total_score)
                vetoes_text = "; ".join(
                    part for part in [vetoes_text, *(f"penalty: {n}" for n in penalty_notes)] if part
                )

            self._record_setup_attempt(
                repository, trade_direction, entry_hint, current_candle
            )
            if swing_high_price is not None and swing_low_price is not None:
                leg = abs(swing_high_price - swing_low_price)
                if leg > 0:
                    decimals = get_instrument(symbol).price_decimals
                    potential_setup["measured_move"] = round(leg, decimals)

            # Assemble the professional trade-plan context for the alert.
            try:
                from src.analysis.pivots import current_session_label

                sweep_desc = None
                if latest_sweep is not None:
                    step = int(TIMEFRAME_SECONDS.get(current_candle.timeframe, 60))
                    age_bars = max(
                        0,
                        (int(current_candle.timestamp) - int(latest_sweep["timestamp"])) // step,
                    )
                    sweep_desc = (
                        f"{str(latest_sweep.get('type', '')).replace('_', ' ').title()} "
                        f"{age_bars} bars ago"
                    )
                daily_r_raw = repository.get_kv("risk_daily_r_value")
                daily_r: Optional[str] = None
                if isinstance(daily_r_raw, (str, int, float)):
                    try:
                        daily_r = f"{float(daily_r_raw):+.2f}R"
                    except (TypeError, ValueError):
                        daily_r = None
                potential_setup["plan_context"] = {
                    "structure": current_structure_state,
                    "macro_bias": macro_bias_state,
                    "regime": repository.get_kv("macro_regime"),
                    "session": current_session_label(int(current_candle.timestamp)),
                    "liquidity": sweep_desc,
                    "daily_r": daily_r,
                    "notes": list(potential_setup.get("confluence_notes", [])),
                }
            except Exception as exc:
                logging.debug("Trade plan context skipped: %s", exc)

            if classification == "ACTIONABLE":
                extra_veto = None
                if str(app_settings.MOOD_FILTER).lower() == "block":
                    extra_veto = mood_block_reason(recent_candles)
                if extra_veto is None and app_settings.SECOND_OPINION_ENABLED:
                    extra_veto = second_opinion_veto(
                        {
                            "symbol": symbol,
                            "strategy": str(setup_strategy or "SMC_ZONE"),
                            "direction": trade_direction,
                            "score": int(total_score),
                            "timestamp": int(current_candle.timestamp),
                        }
                    )
                if extra_veto:
                    classification = "REJECTED"
                    vetoes_text = "; ".join(part for part in [extra_veto, vetoes_text] if part)

            repository.set_kv(state_key("latest_setup_score", symbol), total_score)
            repository.set_kv(
                state_key("latest_setup_classification", symbol), classification
            )
            try:
                repository.log_setup(
                    symbol=symbol,
                    strategy=str(setup_strategy or "SMC_ZONE"),
                    direction=trade_direction,
                    order_type=setup_order_type,
                    score=int(total_score),
                    classification=str(classification),
                    vetoes=vetoes_text,
                    timestamp=int(current_candle.timestamp),
                    levels=(
                        None
                        if classification == "ACTIONABLE"
                        else self._hypothetical_levels(potential_setup, recent_candles, symbol)
                    ),
                )
            except Exception as exc:
                logging.debug("Setup funnel logging skipped: %s", exc)
            logging.info(
                "Setup scored: symbol=%s direction=%s score=%d classification=%s",
                symbol,
                potential_setup["trade_direction"],
                total_score,
                classification,
            )
            if classification == "ACTIONABLE":
                try:
                    signal_saved, signal_errors = self._persist_actionable_signal(
                        repository,
                        cast(dict[str, Any], potential_setup),
                        recent_candles,
                        current_candle,
                        total_score,
                        price_source=price_source,
                    )
                    if signal_saved:
                        signals_generated += 1
                    errors_encountered += signal_errors
                except Exception as exc:
                    errors_encountered += 1
                    logging.error("Failed to persist actionable signal: %s", exc)

        # Release large pulse objects before the next symbol.
        del recent_fvgs
        del recent_candles
        del valid_candles
        return signals_generated, errors_encountered

    def _flush_outbox(self, repository: Repository) -> None:
        if not uses_outbox(repository):
            return
        try:
            flusher = OutboxFlusher(repository, client_factory=self.telegram_client_factory)
            flusher.recover_interrupted()
            delivered = flusher.flush()
            if delivered:
                logging.info("Delivered %d queued Telegram message(s)", delivered)
            repository.prune_outbox()
        except Exception as exc:
            logging.warning("Outbox flush skipped: %s", exc)

    def run(self, force_signal: bool = False) -> None:
        logging.info("---- Pulse started ----")
        structured_logger = self.structured_logger or StructuredLogger()
        self.memory_profiler.start_timer()
        self.memory_profiler.log_snapshot("Pulse start")

        timeframe = SIGNAL_TIMEFRAME
        repository: Optional[Repository] = None
        signals_generated = 0
        errors_encountered = 0
        self._run_feed = {}
        try:
            repository = self.repository_factory()
            self._flush_outbox(repository)
            # --- Macro Regime Check (24-hour gated) ---
            try:
                self._run_macro_regime_check(repository)
            except MacroDataError as exc:
                errors_encountered += 1
                logging.error("Macro regime check failed: %s", exc)
            except Exception as exc:
                errors_encountered += 1
                logging.error("Unexpected macro regime error: %s", exc)
            if os.getenv("MOCK_INGESTION") == "1":
                client: Any = self._mock_client(repository)
                symbols = [os.getenv("MOCK_SYMBOL", "XAUUSD")]
            else:
                client = self.client_factory(repository)
                symbols = active_symbols()
            logging.info("Selected ingestion client: %s", client.__class__.__name__)
            validator = DataValidator()
            self._load_auto_quarantine(repository)

            for index, symbol in enumerate(symbols):
                try:
                    instrument = get_instrument(symbol)
                    symbol_timeframe = instrument.signal_timeframe or timeframe
                    sym_signals, sym_errors = self._pulse_symbol(
                        repository,
                        client,
                        validator,
                        symbol,
                        symbol_timeframe,
                        # UAT force-signal fires once, on the primary symbol only.
                        force_signal and index == 0,
                    )
                    signals_generated += sym_signals
                    errors_encountered += sym_errors
                except Exception:
                    errors_encountered += 1
                    logging.exception("Symbol pulse failed: %s", symbol)

            # Housekeeping runs even on quiet pulses (weekends included).
            self._maybe_prune_market_data(repository)
            self._maybe_refresh_news_calendar(repository)
            self._maybe_post_news_pauses(repository)
            self._maybe_post_morning_briefing(repository)
            self._maybe_post_weekly_card(repository)
            self._maybe_post_weekly_lesson(repository)
            self._maybe_send_weekly_report(repository)
            self._maybe_update_strategy_quarantine(repository)
            self._maybe_check_live_vs_history(repository)
            self._maybe_send_monthly_risk_review(repository)
            self._maybe_send_daily_status(repository)
        except Exception:
            errors_encountered += 1
            logging.exception("Pulse failed unexpectedly")
            raise
        finally:
            execution_time_ms_raw = self.memory_profiler.stop_timer()
            peak_memory_mb_raw = self.memory_profiler.get_peak_memory_mb()
            try:
                execution_time_ms = float(execution_time_ms_raw)
            except (TypeError, ValueError):
                execution_time_ms = 0.0
            try:
                peak_memory_mb = float(peak_memory_mb_raw)
            except (TypeError, ValueError):
                peak_memory_mb = 0.0
            telemetry_data = {
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "execution_time_ms": round(execution_time_ms, 2),
                "peak_memory_mb": round(peak_memory_mb, 2),
                "signals_generated": int(signals_generated),
                "errors_encountered": int(errors_encountered),
            }
            try:
                structured_logger.log_pulse_telemetry(telemetry_data)
            except Exception as exc:
                logging.error("Telemetry logging failed: %s", exc)

            self.memory_profiler.log_snapshot("Pulse end")
            logging.info("Pulse finished in %.2fs", execution_time_ms / 1000.0)
            if repository is not None and OPS_TELEMETRY_ENABLED and hasattr(repository, "record_pulse_run"):
                try:
                    repository.record_pulse_run(
                        int(time.time()),
                        int(execution_time_ms),
                        int(signals_generated),
                        int(errors_encountered),
                        json.dumps(self._run_feed, separators=(",", ":")),
                    )
                except Exception as exc:
                    logging.debug("Run record skipped: %s", exc)
            self._record_pulse_health(repository, errors_encountered)
            if repository is not None and hasattr(repository, "close"):
                repository.close()
            logging.info("---- Pulse ended ----")
