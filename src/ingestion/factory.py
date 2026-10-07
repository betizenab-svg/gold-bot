from __future__ import annotations

import json
import logging
import time
from statistics import median
from typing import Any, Callable, Dict, List, Optional

from config.instruments import INSTRUMENTS
from config.settings import PRICE_BASIS_MAX_AGE_HOURS, SPOT_FEED_ENABLED, TIMEFRAME_SECONDS, TWELVEDATA_API_KEY
from src.domain.candle import Candle
from src.ingestion.twelvedata import DataIngestionError as TwelveDataError
from src.ingestion.twelvedata import TwelveDataClient
from src.ingestion.yahoo_client import DataIngestionError as YahooDataError
from src.ingestion.yahoo_client import YahooFinanceClient
from src.persistence.repository import Repository
from src.resilience.circuit_breaker import CircuitBreaker

SOURCE_SPOT = "SPOT"
SOURCE_BACKUP = "BACKUP"
SOURCE_YAHOO = "YAHOO"
SOURCE_TWELVEDATA = "TWELVEDATA"
SOURCE_NONE = "NONE"
SOURCE_WAIT = "WAIT"

BASIS_CHECK_SECONDS = 3600
_YAHOO_INTERVALS = {"M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h"}


def get_market_data_client(repository: Repository):
    """Legacy single-provider choice (kept for scripts and old tests)."""
    circuit_breaker = CircuitBreaker(repository)
    active_provider = (repository.get_kv("active_provider") or "PRIMARY").upper()

    if active_provider == "SECONDARY":
        if not circuit_breaker.is_open("TWELVEDATA"):
            return TwelveDataClient(repository, circuit_breaker)
        if not circuit_breaker.is_open("YAHOO"):
            return YahooFinanceClient(repository, circuit_breaker)
        return TwelveDataClient(repository, circuit_breaker)

    if circuit_breaker.is_open("YAHOO"):
        return TwelveDataClient(repository, circuit_breaker)

    return YahooFinanceClient(repository, circuit_breaker)


def basis_key(symbol: str) -> str:
    return f"price_basis:{str(symbol).upper()}"


def feed_kind_key(symbol: str) -> str:
    return f"price_feed_kind:{str(symbol).upper()}"


def spot_feed_configured(symbol: str) -> bool:
    instrument = INSTRUMENTS.get(str(symbol).upper())
    return bool(
        SPOT_FEED_ENABLED
        and TWELVEDATA_API_KEY
        and instrument is not None
        and instrument.spot_source == "TWELVEDATA"
        and instrument.spot_symbol
    )


class MarketDataRouter:
    """Chooses each market's price source per call.

    Markets with a spot feed (gold) read it first; when it fails, Yahoo's
    futures prices are used as a backup, shifted by the last measured
    futures-vs-spot gap so levels do not jump. Without a fresh gap the market
    is skipped for that run rather than fed mismatched prices.
    """

    def __init__(
        self,
        repository: Repository,
        yahoo_client: Any = None,
        spot_client: Any = None,
        futures_closes: Optional[Callable[[str, str], Dict[int, float]]] = None,
    ) -> None:
        self.repository = repository
        self.circuit_breaker = CircuitBreaker(repository)
        self._yahoo = yahoo_client
        self._spot = spot_client
        self._spot_injected = spot_client is not None
        self._futures_closes = futures_closes or _yahoo_recent_closes
        self.last_source: Dict[str, str] = {}

    def _yahoo_client(self) -> Any:
        if self._yahoo is None:
            self._yahoo = YahooFinanceClient(self.repository, self.circuit_breaker)
        return self._yahoo

    def _spot_client(self) -> Any:
        if self._spot is None:
            try:
                self._spot = TwelveDataClient(self.repository, self.circuit_breaker)
            except TwelveDataError:
                return None
        return self._spot

    def uses_spot(self, symbol: str) -> bool:
        if self._spot_injected:
            instrument = INSTRUMENTS.get(str(symbol).upper())
            return instrument is not None and bool(instrument.spot_source)
        return spot_feed_configured(symbol)

    def _bar_due(self, symbol: str, timeframe: str) -> bool:
        seconds = int(TIMEFRAME_SECONDS.get(timeframe, 0) or 0)
        if seconds < 3600:
            return True
        try:
            last = int(self.repository.get_kv(f"last_processed_{str(symbol).upper()}") or 0)
        except (TypeError, ValueError):
            return True
        return last <= 0 or time.time() >= last + 2 * seconds

    def fetch_latest_candles(self, symbol: str, timeframe: str) -> List[Candle]:
        if not self._bar_due(symbol, timeframe):
            # Slow charts: no new closed candle can exist yet, so save the call
            # (and TwelveData's free daily credits).
            self.last_source[symbol] = SOURCE_WAIT
            return []
        if self.uses_spot(symbol):
            return self._fetch_spot_market(symbol, timeframe)

        if self.circuit_breaker.is_open("YAHOO") and TWELVEDATA_API_KEY:
            client = self._spot_client()
            if client is not None:
                self.last_source[symbol] = SOURCE_TWELVEDATA
                return client.fetch_latest_candles(symbol, timeframe)

        candles = self._yahoo_client().fetch_latest_candles(symbol, timeframe)
        self.last_source[symbol] = SOURCE_YAHOO
        return candles

    def fetch_history(self, symbol: str, timeframe: str, since: int) -> List[Candle]:
        """Closed spot candles since `since` in one request (one TwelveData
        credit). Empty when the market has no spot feed: futures prices must
        never be mixed into a spot chart."""
        if not self.uses_spot(symbol) or self.circuit_breaker.is_open("TWELVEDATA"):
            return []
        spot = self._spot_client()
        if spot is None:
            return []
        return spot.fetch_latest_candles(symbol, timeframe, since=since)

    def _fetch_spot_market(self, symbol: str, timeframe: str) -> List[Candle]:
        spot = self._spot_client()
        if spot is not None and not self.circuit_breaker.is_open("TWELVEDATA"):
            try:
                candles = spot.fetch_latest_candles(symbol, timeframe)
                self.last_source[symbol] = SOURCE_SPOT
                self._maybe_measure_basis(symbol, timeframe)
                return candles
            except TwelveDataError as exc:
                logging.warning("Spot feed failed for %s: %s; trying backup prices", symbol, exc)

        if self.repository.get_kv(feed_kind_key(symbol)) != SOURCE_SPOT:
            # Never switched to spot yet: Yahoo is still this market's normal feed.
            candles = self._yahoo_client().fetch_latest_candles(symbol, timeframe)
            self.last_source[symbol] = SOURCE_YAHOO
            return candles

        basis = self.fresh_basis(symbol)
        if basis is None:
            logging.warning(
                "No recent futures-vs-spot gap for %s; skipping backup prices this run", symbol
            )
            self.last_source[symbol] = SOURCE_NONE
            return []

        try:
            candles = self._yahoo_client().fetch_latest_candles(symbol, timeframe)
        except YahooDataError:
            self.last_source[symbol] = SOURCE_NONE
            raise
        self.last_source[symbol] = SOURCE_BACKUP
        return [
            Candle(
                symbol=c.symbol,
                timeframe=c.timeframe,
                timestamp=c.timestamp,
                open=c.open - basis,
                high=c.high - basis,
                low=c.low - basis,
                close=c.close - basis,
                volume=c.volume,
            )
            for c in candles
        ]

    def fresh_basis(self, symbol: str) -> Optional[float]:
        raw = self.repository.get_kv(basis_key(symbol))
        if not isinstance(raw, str) or not raw:
            return None
        try:
            payload = json.loads(raw)
            value = float(payload["value"])
            measured_at = int(payload["measured_at"])
        except (TypeError, ValueError, KeyError):
            return None
        if time.time() - measured_at > PRICE_BASIS_MAX_AGE_HOURS * 3600:
            return None
        return value

    def _maybe_measure_basis(self, symbol: str, timeframe: str) -> None:
        """Once an hour: futures close minus spot close on matching candles."""
        now = int(time.time())
        check_key = f"price_basis_checked_at:{str(symbol).upper()}"
        try:
            last = int(self.repository.get_kv(check_key) or 0)
        except (TypeError, ValueError):
            last = 0
        if now - last < BASIS_CHECK_SECONDS:
            return
        self.repository.set_kv(check_key, str(now))
        try:
            spot_closes = {
                c.timestamp: c.close
                for c in self.repository.get_recent_candles(symbol, timeframe, 48)
            }
            futures = self._futures_closes(symbol, timeframe)
            gaps = [futures[ts] - spot_closes[ts] for ts in futures if ts in spot_closes]
            if len(gaps) < 3:
                return
            self.repository.set_kv(
                basis_key(symbol),
                json.dumps(
                    {"value": round(median(gaps), 4), "measured_at": now, "samples": len(gaps)}
                ),
            )
        except Exception as exc:
            logging.info("Futures-vs-spot gap check skipped for %s: %s", symbol, exc)


def _yahoo_recent_closes(symbol: str, timeframe: str) -> Dict[int, float]:
    import pandas as pd
    import yfinance as yf

    instrument = INSTRUMENTS.get(str(symbol).upper())
    interval = _YAHOO_INTERVALS.get(timeframe)
    if instrument is None or interval is None:
        return {}
    frame = yf.download(
        tickers=instrument.yahoo_ticker,
        interval=interval,
        period="2d",
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if frame is None or frame.empty:
        return {}
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    closes = frame["Close"].dropna()
    index = pd.DatetimeIndex(closes.index)
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    return {int(ts.timestamp()): float(value) for ts, value in zip(index, closes.tolist())}


def get_market_router(repository: Repository) -> MarketDataRouter:
    return MarketDataRouter(repository)
