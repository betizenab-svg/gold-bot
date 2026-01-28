from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

from config.settings import OANDA_ACCOUNT_ID, OANDA_API_KEY, OANDA_BASE_URL
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.resilience.circuit_breaker import CircuitBreaker


class DataIngestionError(RuntimeError):
    pass


class OandaClient:
    def __init__(self, repository: Repository, circuit_breaker: Optional[CircuitBreaker] = None) -> None:
        self.repository = repository
        self.circuit_breaker = circuit_breaker or CircuitBreaker(repository)
        self.api_key = OANDA_API_KEY
        self.account_id = OANDA_ACCOUNT_ID
        self.base_url = OANDA_BASE_URL.rstrip("/")

        if not self.api_key:
            raise DataIngestionError("OANDA_API_KEY is not configured")
        if not self.account_id:
            raise DataIngestionError("OANDA_ACCOUNT_ID is not configured")

    def _get_last_timestamp(self, symbol: str, timeframe: str) -> int:
        key = f"last_fetch_{symbol}_{timeframe}"
        value = self.repository.get_kv(key)
        if value is None:
            default_time = datetime.now(timezone.utc) - timedelta(hours=24)
            return int(default_time.timestamp())
        try:
            return int(value)
        except ValueError:
            default_time = datetime.now(timezone.utc) - timedelta(hours=24)
            return int(default_time.timestamp())

    def _format_from_timestamp(self, timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _map_timeframe(self, timeframe: str) -> str:
        mapping = {
            "M1": "M1",
            "M5": "M5",
            "M15": "M15",
            "M30": "M30",
            "H1": "H1",
            "H2": "H2",
            "H4": "H4",
            "H6": "H6",
            "H8": "H8",
            "H12": "H12",
            "D": "D",
            "W": "W",
            "M": "M",
        }
        if timeframe not in mapping:
            raise DataIngestionError(f"Unsupported timeframe: {timeframe}")
        return mapping[timeframe]

    def _normalize_instrument(self, symbol: str) -> str:
        if symbol == "XAUUSD":
            return "XAU_USD"
        return symbol

    def fetch_latest_candles(self, symbol: str, timeframe: str) -> List[Candle]:
        if self.circuit_breaker.is_open("OANDA"):
            return []

        last_timestamp = self._get_last_timestamp(symbol, timeframe)
        instrument = self._normalize_instrument(symbol)
        granularity = self._map_timeframe(timeframe)

        url = f"{self.base_url}/v3/instruments/{instrument}/candles"
        params = {
            "granularity": granularity,
            "from": self._format_from_timestamp(last_timestamp),
            "price": "M",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
        except requests.exceptions.RequestException as exc:
            self.circuit_breaker.record_failure("OANDA", "REQUEST_EXCEPTION", str(exc))
            raise DataIngestionError("OANDA request failed") from exc

        if response.status_code != 200:
            self.circuit_breaker.record_failure("OANDA", str(response.status_code), response.text)
            raise DataIngestionError(f"OANDA API error: {response.status_code} {response.text}")

        payload = response.json()
        candles: List[Candle] = []
        for item in payload.get("candles", []):
            mid = item.get("mid") or {}
            timestamp = self._parse_timestamp(item.get("time"))
            candle = Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=timestamp,
                open=float(mid.get("o", 0)),
                high=float(mid.get("h", 0)),
                low=float(mid.get("l", 0)),
                close=float(mid.get("c", 0)),
                volume=float(item.get("volume", 0)),
            )
            candles.append(candle)

        self.circuit_breaker.record_success("OANDA")
        return candles

    def _parse_timestamp(self, time_value: Optional[str]) -> int:
        if not time_value:
            raise DataIngestionError("Candle timestamp missing")
        try:
            parsed = datetime.fromisoformat(time_value.replace("Z", "+00:00"))
            return int(parsed.timestamp())
        except ValueError as exc:
            raise DataIngestionError(f"Invalid timestamp format: {time_value}") from exc
