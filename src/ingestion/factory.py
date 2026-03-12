from __future__ import annotations

from src.ingestion.twelvedata import TwelveDataClient
from src.ingestion.yahoo_client import YahooFinanceClient
from src.persistence.repository import Repository
from src.resilience.circuit_breaker import CircuitBreaker


def get_market_data_client(repository: Repository):
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
