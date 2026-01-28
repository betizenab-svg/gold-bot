from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
