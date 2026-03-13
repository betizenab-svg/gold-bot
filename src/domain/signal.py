from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Signal:
    symbol: str
    signal_type: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    score: int
    reasoning: str
    timestamp: int
    signal_hash: str
    telegram_message_id: str | None = None
    telegram_chat_id: str | None = None
    closure_reason: str | None = None

    def __post_init__(self) -> None:
        timestamp = self.timestamp
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            timestamp_value = int(timestamp.timestamp())
        else:
            timestamp_value = int(timestamp)
        object.__setattr__(self, "timestamp", timestamp_value)

    @property
    def entry(self) -> float:
        return self.entry_price

    @property
    def sl(self) -> float:
        return self.sl_price

    @property
    def tp1(self) -> float:
        return self.tp1_price

    @property
    def tp2(self) -> float:
        return self.tp2_price
