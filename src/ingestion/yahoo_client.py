from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
import logging

import pandas as pd
import yfinance as yf

from config.settings import YAHOO_SYMBOL_MAP
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
        for key in (f"last_fetch_{symbol}_{timeframe}", "last_processed_timestamp"):
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
        if getattr(normalized.index, "tz", None) is None:
            normalized.index = normalized.index.tz_localize("UTC")
        else:
            normalized.index = normalized.index.tz_convert("UTC")
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
        return aggregated.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()

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
            self.circuit_breaker.record_success("YAHOO")
            return []

        normalized = self._normalize_columns(frame)
        normalized = self._normalize_index_to_utc(normalized)

        required_columns = ["Open", "High", "Low", "Close", "Volume"]
        missing_columns = [column for column in required_columns if column not in normalized.columns]
        if missing_columns:
            self.circuit_breaker.record_failure("YAHOO", "MALFORMED_RESPONSE", ",".join(missing_columns))
            raise DataIngestionError(f"Yahoo Finance response missing columns: {', '.join(missing_columns)}")

        normalized = normalized[required_columns]
        normalized = self._aggregate_frame(normalized, resample_rule)
        normalized = normalized.dropna(subset=["Open", "High", "Low", "Close"])

        candles: List[Candle] = []
        for timestamp, row in normalized.iterrows():
            epoch_timestamp = int(timestamp.timestamp())
            if epoch_timestamp <= last_timestamp:
                continue

            volume = 0.0 if pd.isna(row["Volume"]) else float(row["Volume"])
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=epoch_timestamp,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=volume,
                )
            )

        validator = DataValidator()
        valid_candles = validator.filter_candles(candles)
        dropped = len(candles) - len(valid_candles)
        if dropped:
            logging.info("Dropped %s invalid candles", dropped)

        self.circuit_breaker.record_success("YAHOO")
        return valid_candles