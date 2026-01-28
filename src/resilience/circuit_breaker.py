from __future__ import annotations

import time
from typing import Optional

from src.persistence.repository import Repository


class CircuitBreaker:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def _get_int(self, key: str, default: int = 0) -> int:
        value = self.repository.get_kv(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def is_open(self, provider: str) -> bool:
        state = (self.repository.get_kv("cb_state") or "CLOSED").upper()
        cooldown_until = self._get_int("cb_cooldown_until", 0)
        now = int(time.time())

        if state == "OPEN" and now < cooldown_until:
            return True

        if state == "OPEN" and now >= cooldown_until:
            self.repository.set_kv("cb_state", "CLOSED")
            self.repository.set_kv("cb_failure_count", 0)
            self.repository.set_kv("cb_cooldown_until", 0)
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

        failure_count = self._get_int("cb_failure_count", 0) + 1
        self.repository.set_kv("cb_failure_count", failure_count)

        if failure_count >= 3:
            self.repository.set_kv("cb_state", "OPEN")
            self.repository.set_kv("cb_cooldown_until", now + 900)
            self.repository.set_kv("active_provider", "SECONDARY")

    def record_success(self, provider: str) -> None:
        self.repository.set_kv("cb_failure_count", 0)
        self.repository.set_kv("cb_state", "CLOSED")
        self.repository.set_kv("cb_cooldown_until", 0)
        self.repository.set_kv("active_provider", "PRIMARY")
