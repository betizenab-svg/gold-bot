from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gc
from typing import Any, List, Optional, Tuple, cast
import logging

import pandas as pd
import yfinance as yf

from config.settings import TIMEFRAME_SECONDS, YAHOO_SYMBOL_MAP
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.resilience.circuit_breaker import CircuitBreaker
from src.validation.validator import DataValidator


class DataIngestionError(RuntimeError):
    pass


class YahooFinanceClient:
    def __init__(self, repository: Repository, circuit_breaker: Optional[CircuitBreaker] = None) -> None:
        self.repository = repository
        self.circuit_breaker = circuit_breaker or CircuitBreaker(repository)

    def _limit_start_timestamp(self, timestamp: int, interval: str) -> int:
        now = int(datetime.now(timezone.utc).timestamp())

        if interval == "1m":
            minimum_timestamp = now - 7 * 24 * 3600
        elif interval in {"5m", "15m", "30m", "1h"}:
            minimum_timestamp = now - 60 * 24 * 3600
        else:
            minimum_timestamp = 0

        limited_timestamp = max(timestamp, minimum_timestamp)
        if limited_timestamp >= now:
            return max(now - 60, 0)
        return limited_timestamp

    def _get_last_timestamp(self, symbol: str, timeframe: str) -> int:
        keys = [f"last_fetch_{symbol}_{timeframe}", f"last_processed_{symbol}"]
        if symbol == "XAUUSD":
            keys.append("last_processed_timestamp")  # legacy global watermark
        for key in keys:
            value = self.repository.get_kv(key)
            if value is None:
                continue
            try:
                return int(value)
            except ValueError:
                continue

        default_time = datetime.now(timezone.utc) - timedelta(hours=24)
        return int(default_time.timestamp())

    def _map_timeframe(self, timeframe: str) -> Tuple[str, Optional[str]]:
        mapping = {
            "M1": ("1m", None),
            "M5": ("5m", None),
            "M15": ("15m", None),
            "M30": ("30m", None),
            "H1": ("1h", None),
            "H2": ("1h", "2h"),
            "H4": ("1h", "4h"),
            "H6": ("1h", "6h"),
            "H8": ("1h", "8h"),
            "H12": ("1h", "12h"),
            "D": ("1d", None),
            "W": ("1wk", None),
        }
        if timeframe not in mapping:
            raise DataIngestionError(f"Unsupported timeframe: {timeframe}")
        return mapping[timeframe]

    def _normalize_symbol(self, symbol: str) -> str:
        return YAHOO_SYMBOL_MAP.get(symbol, symbol)

    def _normalize_columns(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.copy()
        if isinstance(normalized.columns, pd.MultiIndex):
            normalized.columns = normalized.columns.get_level_values(0)
        return normalized

    def _normalize_index_to_utc(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.copy()
        dti = pd.DatetimeIndex(normalized.index)
        if getattr(dti, "tz", None) is None:
            normalized.index = dti.tz_localize("UTC")
        else:
            normalized.index = dti.tz_convert("UTC")
        return normalized.sort_index()

    def _aggregate_frame(self, frame: pd.DataFrame, resample_rule: Optional[str]) -> pd.DataFrame:
        if not resample_rule:
            return frame

        aggregated = frame.resample(resample_rule, label="right", closed="right").agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        cols = ["Open", "High", "Low", "Close"]
        mask: Any = cast(pd.DataFrame, aggregated[cols]).notna().all(axis=1)
        return cast(pd.DataFrame, aggregated[mask]).sort_index()

    def fetch_latest_candles(self, symbol: str, timeframe: str) -> List[Candle]:
        if self.circuit_breaker.is_open("YAHOO"):
            return []

        last_timestamp = self._get_last_timestamp(symbol, timeframe)
        provider_symbol = self._normalize_symbol(symbol)
        interval, resample_rule = self._map_timeframe(timeframe)
        request_start_timestamp = self._limit_start_timestamp(last_timestamp, interval)
        start_at = datetime.fromtimestamp(request_start_timestamp, timezone.utc)

        try:
            frame = yf.download(
                tickers=provider_symbol,
                interval=interval,
                start=start_at,
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception as exc:
            self.circuit_breaker.record_failure("YAHOO", "REQUEST_EXCEPTION", str(exc))
            raise DataIngestionError("Yahoo Finance request failed") from exc

        if frame is None or frame.empty:
            message = f"Yahoo Finance returned empty data for {provider_symbol}"
            self.circuit_breaker.record_failure("YAHOO", "EMPTY_RESPONSE", message)
            raise DataIngestionError(message)

        df = self._normalize_columns(frame)
        df = self._normalize_index_to_utc(df)

        required_columns = ["Open", "High", "Low", "Close", "Volume"]
        missing_columns = [column for column in required_columns if column not in df.columns]
        if missing_columns:
            self.circuit_breaker.record_failure("YAHOO", "MALFORMED_RESPONSE", ",".join(missing_columns))
            raise DataIngestionError(f"Yahoo Finance response missing columns: {', '.join(missing_columns)}")

        df = cast(pd.DataFrame, df[required_columns])
        df = self._aggregate_frame(df, resample_rule)
        ohlc_cols = ["Open", "High", "Low", "Close"]
        df = df[df[ohlc_cols].notna().all(axis=1)]

        candles: List[Candle] = []
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        step_seconds = int(TIMEFRAME_SECONDS.get(timeframe, 60))
        for timestamp, row in df.iterrows():
            row_data: Any = row
            epoch_timestamp = int(cast(Any, timestamp).timestamp())
            if epoch_timestamp <= last_timestamp:
                continue
            # Skip the still-forming bar: its OHLC would be frozen wrong forever.
            if epoch_timestamp + step_seconds > now_epoch:
                continue

            volume = 0.0 if pd.isna(row_data["Volume"]) else float(row_data["Volume"])
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=epoch_timestamp,
                    open=float(row_data["Open"]),
                    high=float(row_data["High"]),
                    low=float(row_data["Low"]),
                    close=float(row_data["Close"]),
                    volume=volume,
                )
            )

        del df
        gc.collect()

        validator = DataValidator()
        valid_candles = validator.filter_candles(candles)
        dropped = len(candles) - len(valid_candles)
        if dropped:
            logging.info("Dropped %s invalid candles", dropped)

        self.circuit_breaker.record_success("YAHOO")
        return valid_candles