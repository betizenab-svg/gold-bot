from __future__ import annotations

import json
import logging
from typing import Any, Optional

from config.settings import (
    NEWS_BLACKOUT_AFTER_MIN,
    NEWS_BLACKOUT_BEFORE_MIN,
    RISK_CONSECUTIVE_SL_HALT,
    RISK_DAILY_MAX_LOSS_R,
    RISK_DAILY_PROFIT_LOCK_R,
    RISK_HALT_HOURS,
    RISK_MAX_CONCURRENT_SIGNALS,
    RISK_MAX_SIGNALS_PER_DAY,
    RISK_SL_COOLDOWN_MINUTES,
    RISK_TIER2_CONSECUTIVE_SL,
)

KV_LAST_SL_TIMESTAMP = "risk_last_sl_timestamp"
KV_CONSECUTIVE_SL_COUNT = "risk_consecutive_sl_count"
KV_DAILY_R_DATE = "risk_daily_r_date"
KV_DAILY_R_VALUE = "risk_daily_r_value"
KV_NEWS_EVENTS = "upcoming_news_events_json"


def _safe_int(raw_value: Any, default: int = 0) -> int:
    try:
        return int(str(raw_value))
    except (TypeError, ValueError):
        return default


def _safe_float(raw_value: Any, default: float = 0.0) -> float:
    try:
        return float(str(raw_value))
    except (TypeError, ValueError):
        return default


class RiskGovernor:
    """Hard operational limits so one bad day cannot become a blown account.

    - caps signals per day
    - cooldown after any stop loss
    - full halt after a losing streak
    - caps concurrently open signals
    """

    def __init__(
        self,
        max_signals_per_day: int = RISK_MAX_SIGNALS_PER_DAY,
        sl_cooldown_minutes: int = RISK_SL_COOLDOWN_MINUTES,
        consecutive_sl_halt: int = RISK_CONSECUTIVE_SL_HALT,
        halt_hours: int = RISK_HALT_HOURS,
        max_concurrent_signals: int = RISK_MAX_CONCURRENT_SIGNALS,
    ) -> None:
        self.max_signals_per_day = int(max_signals_per_day)
        self.sl_cooldown_minutes = int(sl_cooldown_minutes)
        self.consecutive_sl_halt = int(consecutive_sl_halt)
        self.halt_hours = int(halt_hours)
        self.max_concurrent_signals = int(max_concurrent_signals)

    def is_trading_allowed(self, repository: Any, now_ts: int) -> tuple[bool, str]:
        now_ts = int(now_ts)

        try:
            paused = repository.get_kv("trading_paused")
            if isinstance(paused, str) and paused.strip() in {"1", "true", "TRUE", "yes"}:
                return False, "Risk governor: trading manually paused (kill switch)"
        except Exception as exc:
            logging.debug("Risk governor pause check skipped: %s", exc)

        try:
            open_signals = repository.get_open_signals()
            if isinstance(open_signals, list) and len(open_signals) >= self.max_concurrent_signals:
                return False, (
                    f"Risk governor: {len(open_signals)} signals already open "
                    f"(max {self.max_concurrent_signals})"
                )
        except Exception as exc:
            logging.debug("Risk governor open-signal check skipped: %s", exc)

        try:
            day_start = now_ts - (now_ts % 86400)
            todays_signals = repository.count_signals_since(day_start)
            if isinstance(todays_signals, int) and todays_signals >= self.max_signals_per_day:
                return False, (
                    f"Risk governor: daily signal cap reached "
                    f"({todays_signals}/{self.max_signals_per_day})"
                )
        except Exception as exc:
            logging.debug("Risk governor daily-cap check skipped: %s", exc)

        last_sl_ts = self._read_kv_int(repository, KV_LAST_SL_TIMESTAMP)
        if last_sl_ts is not None and last_sl_ts > 0:
            elapsed = now_ts - last_sl_ts
            streak = self._read_kv_int(repository, KV_CONSECUTIVE_SL_COUNT) or 0

            if streak >= RISK_TIER2_CONSECUTIVE_SL and elapsed < 24 * 3600:
                return False, (
                    f"Risk governor: tier-2 halt after {streak} consecutive stop losses "
                    "(suspended 24h; review parameters)"
                )
            if streak >= self.consecutive_sl_halt and elapsed < self.halt_hours * 3600:
                return False, (
                    f"Risk governor: halted after {streak} consecutive stop losses "
                    f"(resumes after {self.halt_hours}h)"
                )
            if 0 <= elapsed < self.sl_cooldown_minutes * 60:
                return False, (
                    f"Risk governor: cooling down after a stop loss "
                    f"({self.sl_cooldown_minutes}min window)"
                )

        daily_r = self._read_daily_r(repository, now_ts)
        if daily_r is not None:
            if daily_r <= -abs(RISK_DAILY_MAX_LOSS_R):
                return False, (
                    f"Risk governor: daily loss limit reached ({daily_r:+.2f}R); "
                    "no more signals today"
                )
            if daily_r >= abs(RISK_DAILY_PROFIT_LOCK_R):
                return False, (
                    f"Risk governor: daily profit locked in ({daily_r:+.2f}R); "
                    "protecting the day"
                )

        blackout_reason = self._news_blackout_reason(repository, now_ts)
        if blackout_reason:
            return False, blackout_reason

        return True, "Risk governor: trading allowed"

    def _news_blackout_reason(self, repository: Any, now_ts: int) -> Optional[str]:
        try:
            raw = repository.get_kv(KV_NEWS_EVENTS)
        except Exception:
            return None
        if not raw or not isinstance(raw, str):
            return None
        try:
            events = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(events, list):
            return None

        for event in events:
            if isinstance(event, dict):
                event_ts = _safe_int(event.get("timestamp"), default=-1)
            else:
                event_ts = _safe_int(event, default=-1)
            if event_ts <= 0:
                continue
            window_start = event_ts - NEWS_BLACKOUT_BEFORE_MIN * 60
            window_end = event_ts + NEWS_BLACKOUT_AFTER_MIN * 60
            if window_start <= now_ts <= window_end:
                return "Risk governor: high-impact news blackout window"
        return None

    def _read_daily_r(self, repository: Any, now_ts: int) -> Optional[float]:
        try:
            stored_date = repository.get_kv(KV_DAILY_R_DATE)
        except Exception:
            return None
        if not isinstance(stored_date, str):
            return None
        today = str(now_ts - (now_ts % 86400))
        if stored_date != today:
            return None
        try:
            raw_value = repository.get_kv(KV_DAILY_R_VALUE)
        except Exception:
            return None
        if raw_value is None or not isinstance(raw_value, (str, int, float)):
            return None
        return _safe_float(raw_value, default=0.0)

    def record_result_r(self, repository: Any, r_delta: float, event_ts: int) -> None:
        """Accumulate realized R for the day (terminal events only)."""
        try:
            today = str(int(event_ts) - (int(event_ts) % 86400))
            current = 0.0
            stored_date = repository.get_kv(KV_DAILY_R_DATE)
            if isinstance(stored_date, str) and stored_date == today:
                current = _safe_float(repository.get_kv(KV_DAILY_R_VALUE), default=0.0)
            repository.set_kv(KV_DAILY_R_DATE, today)
            repository.set_kv(KV_DAILY_R_VALUE, f"{current + float(r_delta):.4f}")
        except Exception as exc:
            logging.debug("Risk daily-R record skipped: %s", exc)

    def record_stop_loss(self, repository: Any, event_ts: int) -> None:
        try:
            streak = self._read_kv_int(repository, KV_CONSECUTIVE_SL_COUNT) or 0
            repository.set_kv(KV_CONSECUTIVE_SL_COUNT, str(streak + 1))
            repository.set_kv(KV_LAST_SL_TIMESTAMP, str(int(event_ts)))
        except Exception as exc:
            logging.debug("Risk governor stop-loss record skipped: %s", exc)

    def record_win(self, repository: Any) -> None:
        try:
            repository.set_kv(KV_CONSECUTIVE_SL_COUNT, "0")
        except Exception as exc:
            logging.debug("Risk governor win record skipped: %s", exc)

    @staticmethod
    def _read_kv_int(repository: Any, key: str) -> Optional[int]:
        try:
            raw = repository.get_kv(key)
        except Exception:
            return None
        if raw is None:
            return None
        value = _safe_int(raw, default=-1)
        return value if value >= 0 else None
