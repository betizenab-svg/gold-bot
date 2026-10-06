from __future__ import annotations

import time
from typing import Optional

from src.persistence.repository import Repository


class CircuitBreaker:
    """Per-provider breaker: 3 failures in a row pause that provider for 15
    minutes. Yahoo keeps the original unsuffixed keys (live state survives)."""

    FAILURE_THRESHOLD = 3
    COOLDOWN_SECONDS = 900

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @staticmethod
    def _key(base: str, provider: str) -> str:
        name = str(provider or "YAHOO").upper()
        return base if name == "YAHOO" else f"{base}:{name}"

    @staticmethod
    def _is_primary(provider: str) -> bool:
        return str(provider or "YAHOO").upper() == "YAHOO"

    def _get_int(self, key: str, default: int = 0) -> int:
        value = self.repository.get_kv(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def is_open(self, provider: str) -> bool:
        state = (self.repository.get_kv(self._key("cb_state", provider)) or "CLOSED").upper()
        cooldown_until = self._get_int(self._key("cb_cooldown_until", provider), 0)
        now = int(time.time())

        if state == "OPEN" and now < cooldown_until:
            return True

        if state == "OPEN" and now >= cooldown_until:
            self.repository.set_kv(self._key("cb_state", provider), "CLOSED")
            self.repository.set_kv(self._key("cb_failure_count", provider), 0)
            self.repository.set_kv(self._key("cb_cooldown_until", provider), 0)
            if self._is_primary(provider):
                self.repository.set_kv("active_provider", "PRIMARY")

        return False

    def record_failure(self, provider: str, error_code: Optional[str], message: str) -> None:
        now = int(time.time())
        self.repository.log_error(
            provider=provider,
            error_code=str(error_code) if error_code is not None else "UNKNOWN",
            message=message,
            timestamp=now,
        )

        failure_count = self._get_int(self._key("cb_failure_count", provider), 0) + 1
        self.repository.set_kv(self._key("cb_failure_count", provider), failure_count)

        if failure_count >= self.FAILURE_THRESHOLD:
            self.repository.set_kv(self._key("cb_state", provider), "OPEN")
            self.repository.set_kv(
                self._key("cb_cooldown_until", provider), now + self.COOLDOWN_SECONDS
            )
            if self._is_primary(provider):
                self.repository.set_kv("active_provider", "SECONDARY")

    def record_success(self, provider: str) -> None:
        self.repository.set_kv(self._key("cb_failure_count", provider), 0)
        self.repository.set_kv(self._key("cb_state", provider), "CLOSED")
        self.repository.set_kv(self._key("cb_cooldown_until", provider), 0)
        if self._is_primary(provider):
            self.repository.set_kv("active_provider", "PRIMARY")
