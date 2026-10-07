from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import List, Optional
import logging

import requests

from config.instruments import INSTRUMENTS
from config.settings import SYMBOL_MAP, TWELVEDATA_API_KEY, TWELVEDATA_BASE_URL
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.resilience.circuit_breaker import CircuitBreaker
from src.validation.validator import DataValidator


class DataIngestionError(RuntimeError):
    pass


class TwelveDataClient:
    # One call costs one credit whatever the size; 2000 M5 bars ~ 7 days.
    MAX_OUTPUTSIZE = 2000

    def __init__(self, repository: Repository, circuit_breaker: Optional[CircuitBreaker] = None) -> None:
        self.repository = repository
        self.circuit_breaker = circuit_breaker or CircuitBreaker(repository)
        self.api_key = TWELVEDATA_API_KEY
        self.base_url = TWELVEDATA_BASE_URL.rstrip("/")

        if not self.api_key:
            raise DataIngestionError("TWELVEDATA_API_KEY is not configured")

    def _get_last_timestamp(self, symbol: str, timeframe: str) -> int:
        keys = [f"last_fetch_{symbol}_{timeframe}", f"last_processed_{symbol}"]
        if symbol == "XAUUSD":
            keys.append("last_processed_timestamp")
        for key in keys:
            value = self.repository.get_kv(key)
            if value is None:
                continue
            try:
                return int(value)
            except ValueError:
                continue
        return int(datetime.now(timezone.utc).timestamp() - 24 * 3600)

    def _map_timeframe(self, timeframe: str) -> str:
        mapping = {
            "M1": "1min",
            "M5": "5min",
            "M15": "15min",
            "M30": "30min",
            "H1": "1h",
            "H4": "4h",
            "D": "1day",
        }
        if timeframe not in mapping:
            raise DataIngestionError(f"Unsupported timeframe: {timeframe}")
        return mapping[timeframe]

    def _timeframe_seconds(self, timeframe: str) -> int:
        mapping = {
            "M1": 60,
            "M5": 300,
            "M15": 900,
            "M30": 1800,
            "H1": 3600,
            "H4": 14400,
            "D": 86400,
        }
        if timeframe not in mapping:
            raise DataIngestionError(f"Unsupported timeframe: {timeframe}")
        return mapping[timeframe]

    def _normalize_symbol(self, symbol: str) -> str:
        instrument = INSTRUMENTS.get(str(symbol).upper())
        if instrument is not None and instrument.spot_symbol:
            return instrument.spot_symbol
        return SYMBOL_MAP.get(symbol, symbol)

    def _parse_timestamp(self, value: str) -> int:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        except ValueError as exc:
            raise DataIngestionError(f"Invalid timestamp format: {value}") from exc

    def _calculate_outputsize(self, last_timestamp: int, timeframe: str) -> int:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        delta = max(now_ts - last_timestamp, 0)
        seconds_per_bar = self._timeframe_seconds(timeframe)
        bars = max(1, ceil(delta / seconds_per_bar) + 1)
        return min(bars, self.MAX_OUTPUTSIZE)

    def fetch_latest_candles(
        self, symbol: str, timeframe: str, since: Optional[int] = None
    ) -> List[Candle]:
        """Closed candles newer than the last stored one (or than `since`)."""
        if self.circuit_breaker.is_open("TWELVEDATA"):
            return []

        last_timestamp = int(since) if since is not None else self._get_last_timestamp(symbol, timeframe)
        interval = self._map_timeframe(timeframe)
        provider_symbol = self._normalize_symbol(symbol)
        outputsize = self._calculate_outputsize(last_timestamp, timeframe)

        url = f"{self.base_url}/time_series"
        params = {
            "symbol": provider_symbol,
            "interval": interval,
            "apikey": self.api_key,
            "outputsize": outputsize,
            "timezone": "UTC",
        }

        try:
            response = requests.get(url, params=params, timeout=15)
        except requests.exceptions.RequestException as exc:
            self.circuit_breaker.record_failure("TWELVEDATA", "REQUEST_EXCEPTION", str(exc))
            raise DataIngestionError("TwelveData request failed") from exc

        if response.status_code != 200:
            self.circuit_breaker.record_failure("TWELVEDATA", str(response.status_code), response.text)
            raise DataIngestionError(f"TwelveData API error: {response.status_code} {response.text}")

        try:
            payload = response.json()
        except ValueError as exc:
            self.circuit_breaker.record_failure("TWELVEDATA", "INVALID_JSON", str(exc))
            raise DataIngestionError("TwelveData returned invalid JSON") from exc

        if isinstance(payload, dict) and str(payload.get("status", "")).lower() == "error":
            code = str(payload.get("code", "ERROR"))
            message = str(payload.get("message", ""))[:300]
            self.circuit_breaker.record_failure("TWELVEDATA", code, message)
            raise DataIngestionError(f"TwelveData error {code}: {message}")

        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            self.circuit_breaker.record_failure("TWELVEDATA", "MALFORMED_RESPONSE", str(payload)[:300])
            raise DataIngestionError("TwelveData response missing values")

        now_epoch = int(datetime.now(timezone.utc).timestamp())
        step_seconds = self._timeframe_seconds(timeframe)
        candles: List[Candle] = []
        for item in values:
            timestamp = self._parse_timestamp(item.get("datetime", ""))
            if timestamp <= last_timestamp:
                continue
            # The newest bar is still forming; storing it would freeze wrong OHLC.
            if timestamp + step_seconds > now_epoch:
                continue
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    open=float(item.get("open", 0)),
                    high=float(item.get("high", 0)),
                    low=float(item.get("low", 0)),
                    close=float(item.get("close", 0)),
                    volume=int(float(item.get("volume", 0) or 0)),
                )
            )
        candles.sort(key=lambda candle: candle.timestamp)

        validator = DataValidator()
        valid_candles = validator.filter_candles(candles)
        dropped = len(candles) - len(valid_candles)
        if dropped:
            logging.info("Dropped %s invalid candles", dropped)

        self.circuit_breaker.record_success("TWELVEDATA")
        return valid_candles
