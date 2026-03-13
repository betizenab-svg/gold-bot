# pyre-ignore-all-errors[21]
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import logging
import os
import time
# Trigger linter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, cast

import numpy as np
import pandas as pd

from config.database import get_connection
from src.core.telemetry import MemoryProfiler
from src.analysis.crisis import CrisisDetector
from src.analysis.regime import RegimeDetector
from src.analysis.sovereign import SovereignProxy
from src.analysis.cot_index import CotAnalyzer
from src.analysis.consensus import SurpriseFactorEngine
from src.analysis.atr import ATREngine
from src.analysis.displacement import DisplacementEngine
from src.analysis.filters import PermissionEngine
from src.analysis.fractals import FractalDetector
from src.analysis.fvg import FVGScanner
from src.analysis.liquidity import LiquiditySweepDetector
from src.analysis.mitigation import ZoneLifecycleManager
from src.analysis.order_block import OrderBlockScanner
from src.analysis.signal_factory import SignalFactory
from src.analysis.scoring import ScoringEngine
from src.analysis.structure import MarketStructureEngine
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
from config.settings import DXY_TICKER, DXY_CORRELATION_WINDOW, TIMEFRAME_SECONDS, FSR_LOOKBACK_PERIOD
from src.analysis.fsr_engine import FSREngine
from src.analysis.bias_engine import MacroBiasAggregator
from src.strategies.inside_bar_trap import InsideBarTrapStrategy
from src.strategies.pin_bar_rejection import PinBarRejectionStrategy


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
        """Aggregate stored XAUUSD candles from market_data into daily closes."""
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

    def _detect_trade_setup(
        self,
        repository: Repository,
        symbol: str,
        current_candle: Candle,
        recent_candles: List[Candle],
    ) -> Optional[dict[str, Any]]:
        active_zones = repository.get_active_zones(symbol)
        pin_bar_strategy = PinBarRejectionStrategy()
        pin_bar_setup = pin_bar_strategy.detect_setup(recent_candles, active_zones)
        if pin_bar_setup is not None:
            return pin_bar_setup

        inside_bar_strategy = InsideBarTrapStrategy()
        inside_bar_setup = inside_bar_strategy.detect_setup(recent_candles)
        if inside_bar_setup is not None:
            return inside_bar_setup

        try:
            rows = repository.connection.execute(
                """
                SELECT id, symbol, timeframe, type, price_top, price_bottom, status, created_at
                FROM zones
                WHERE symbol = ?
                  AND status IN ('ACTIVE', 'UNMITIGATED', 'MITIGATED')
                  AND type IN ('OB_BULLISH', 'OB_BEARISH')
                ORDER BY created_at DESC
                LIMIT 20;
                """,
                (symbol,),
            ).fetchall()
        except Exception:
            return None

        order_blocks = [
            {
                "id": row[0],
                "symbol": row[1],
                "timeframe": row[2],
                "type": row[3],
                "price_top": float(row[4]),
                "price_bottom": float(row[5]),
                "status": row[6],
                "created_at": int(row[7]),
            }
            for row in rows
        ]
        if not order_blocks:
            return None

        for zone in order_blocks:
            trade_direction = self._zone_bounce_direction(current_candle, zone)
            if trade_direction is None:
                continue
            return {
                "trade_direction": trade_direction,
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
    ) -> None:
        repository.set_kv("smc_latest_fractals", json.dumps(latest_fractals))

        swing_high = latest_fractals.get("swing_high")
        swing_low = latest_fractals.get("swing_low")

        if swing_high is not None:
            repository.set_kv("last_swing_high", swing_high)
        if swing_low is not None:
            repository.set_kv("last_swing_low", swing_low)

    def _evaluate_zone_lifecycle(
        self,
        repository: Repository,
        symbol: str,
        current_candle: Candle,
    ) -> None:
        lifecycle_manager = ZoneLifecycleManager()
        active_zones = repository.get_active_zones(symbol)
        updated_zones = lifecycle_manager.evaluate_zones(current_candle, active_zones)
        if not updated_zones:
            return

        repository.update_zone_statuses(updated_zones)
        logging.info("Updated %d zones during lifecycle evaluation", len(updated_zones))

    def _evaluate_market_structure(
        self,
        repository: Repository,
        current_candle: Candle,
    ) -> Optional[str]:
        structure_engine = MarketStructureEngine()

        stored_trend = repository.get_kv("current_structure_state")
        current_trend = stored_trend.upper() if stored_trend else "BULLISH"
        if stored_trend is None:
            repository.set_kv("current_structure_state", current_trend)

        last_swing_high = self._parse_swing_point(repository.get_kv("last_swing_high"))
        last_swing_low = self._parse_swing_point(repository.get_kv("last_swing_low"))

        counter_trend_swing = last_swing_low if current_trend == "BULLISH" else last_swing_high
        new_trend = structure_engine.detect_choch(
            current_candle=current_candle,
            last_counter_trend_swing=counter_trend_swing,
            current_trend=current_trend,
        )
        if new_trend is not None:
            repository.set_kv("current_structure_state", new_trend)
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
        current_candle: Candle,
    ) -> None:
        if not recent_candles:
            return

        last_swing_high_point = self._parse_swing_point(repository.get_kv("last_swing_high"))
        last_swing_low_point = self._parse_swing_point(repository.get_kv("last_swing_low"))
        if last_swing_high_point is None or last_swing_low_point is None:
            return

        detector = LiquiditySweepDetector()
        avg_volume = detector.calculate_average_volume(recent_candles, period=14)
        sweep = detector.detect_sweep(
            current_candle=current_candle,
            avg_volume=avg_volume,
            last_swing_high=float(last_swing_high_point["price"]),
            last_swing_low=float(last_swing_low_point["price"]),
        )
        if sweep is None:
            return

        repository.set_kv("latest_liquidity_sweep", json.dumps(sweep))
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
            rows = repository.connection.execute(
                """
                SELECT id, symbol, timeframe, type, price_top, price_bottom, status, created_at
                FROM zones
                WHERE symbol = ?
                  AND timeframe = ?
                  AND status = 'UNMITIGATED'
                  AND type IN ('FVG_BULLISH', 'FVG_BEARISH')
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (symbol, timeframe, limit),
            ).fetchall()
        except Exception:
            return []

        if not isinstance(rows, list):
            return []

        return [
            {
                "id": row[0],
                "symbol": row[1],
                "timeframe": row[2],
                "type": row[3],
                "price_top": float(row[4]),
                "price_bottom": float(row[5]),
                "status": row[6],
                "created_at": int(row[7]),
            }
            for row in rows
        ]

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

    def _persist_actionable_signal(
        self,
        repository: Repository,
        potential_setup: dict[str, Any],
        recent_candles: List[Candle],
        current_candle: Candle,
        total_score: int,
    ) -> None:
        zone = cast(Optional[dict[str, Any]], potential_setup.get("zone")) or {}
        trade_direction = str(potential_setup["trade_direction"])
        signal_context: dict[str, Any] = dict(zone)
        for key in ("entry_price", "sl_price", "strategy", "trigger", "zone_id"):
            if key in potential_setup:
                signal_context[key] = potential_setup[key]
        if "id" not in signal_context and "zone_id" in potential_setup:
            signal_context["id"] = potential_setup["zone_id"]

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

        if repository.is_signal_duplicate(signal.signal_hash):
            logging.info("Skipped duplicate signal: %s", signal.signal_hash)
            return

        repository.save_signal(signal)
        logging.info("Saved actionable signal: %s", signal.signal_hash)

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
            logging.info("Insufficient data for crisis filter")

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
                        logging.info("FSR Engine skipped: insufficient surprise observations (%d)", len(surprise_observations))
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

    def run(self) -> None:
        logging.info("---- Pulse started ----")
        start_time = time.time()
        self.memory_profiler.log_snapshot("Pulse start")

        symbol = "XAUUSD"
        timeframe = "M1"
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
                candles = client.fetch_latest_candles(symbol, timeframe)
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
            current_candle = max(valid_candles, key=lambda candle: candle.timestamp)
            self._evaluate_zone_lifecycle(repository, symbol, current_candle)

            detector = FractalDetector()
            recent_candles = repository.get_recent_candles(symbol, timeframe, 100)
            latest_fractals = detector.find_fractals(recent_candles)
            self._persist_latest_fractals(repository, latest_fractals)
            self._evaluate_liquidity_sweep(repository, recent_candles, current_candle)
            recent_bos_type = self._evaluate_market_structure(repository, current_candle)
            recent_fvgs = self._scan_for_fvg_zones(repository, recent_candles)
            self._scan_for_order_blocks(
                repository,
                recent_candles,
                recent_bos_type,
                recent_fvgs,
            )

            potential_setup = self._detect_trade_setup(
                repository,
                symbol,
                current_candle,
                recent_candles,
            )
            if potential_setup is not None:
                permission_engine = PermissionEngine()
                macro_context = self._build_macro_permission_context(repository)
                is_permitted, permission_reason = permission_engine.is_trade_permitted(
                    cast(dict[str, Any], potential_setup),
                    macro_context,
                )
                if not is_permitted:
                    logging.info(
                        "Setup blocked by permission filter: direction=%s reason=%s",
                        potential_setup.get("trade_direction"),
                        permission_reason,
                    )
                    elapsed = time.time() - start_time
                    self.memory_profiler.log_snapshot("Pulse end")
                    logging.info("Pulse finished in %.2fs", elapsed)
                    return

                scoring_engine = ScoringEngine()

                raw_macro_bias_state = repository.get_kv("macro_bias_state")
                if raw_macro_bias_state is None:
                    raw_macro_bias_state = repository.get_kv("global_macro_bias")
                macro_bias_state = (
                    raw_macro_bias_state.upper() if raw_macro_bias_state is not None else "NEUTRAL"
                )

                raw_structure_state = repository.get_kv("current_structure_state")
                current_structure_state = (
                    raw_structure_state.upper() if raw_structure_state is not None else "BULLISH"
                )

                latest_sweep = self._parse_liquidity_sweep(repository.get_kv("latest_liquidity_sweep"))
                has_recent_sweep = self._has_recent_directional_sweep(
                    trade_direction=str(potential_setup["trade_direction"]),
                    current_timestamp=int(current_candle.timestamp),
                    timeframe=current_candle.timeframe,
                    latest_sweep=latest_sweep,
                )

                total_score = scoring_engine.calculate_total_score(
                    trade_direction=str(potential_setup["trade_direction"]),
                    macro_bias=macro_bias_state,
                    current_structure=current_structure_state,
                    zone_dict=cast(dict[str, Any], potential_setup.get("zone")),
                    has_recent_sweep=has_recent_sweep,
                )
                classification = scoring_engine.classify_score(total_score)

                repository.set_kv("latest_setup_score", total_score)
                repository.set_kv("latest_setup_classification", classification)
                logging.info(
                    "Setup scored: direction=%s score=%d classification=%s",
                    potential_setup["trade_direction"],
                    total_score,
                    classification,
                )
                if classification == "ACTIONABLE":
                    self._persist_actionable_signal(
                        repository,
                        cast(dict[str, Any], potential_setup),
                        recent_candles,
                        current_candle,
                        total_score,
                    )

            elapsed = time.time() - start_time
            self.memory_profiler.log_snapshot("Pulse end")
            logging.info("Pulse finished in %.2fs", elapsed)
        finally:
            if hasattr(repository, "connection"):
                repository.connection.close()
            logging.info("---- Pulse ended ----")
