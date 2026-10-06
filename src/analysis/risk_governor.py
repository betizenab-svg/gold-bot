from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from config import settings
from config.instruments import get_instrument
from src.analysis.market_hours import minutes_to_weekly_close
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

# No new trades this close to Friday's close when the weekend plan is on.
WEEKEND_NO_NEW_MINUTES = 120


def week_start(now_ts: int) -> int:
    """Monday 00:00 UTC of the week containing now_ts."""
    day_start = int(now_ts) - (int(now_ts) % 86400)
    weekday = datetime.fromtimestamp(day_start, tz=timezone.utc).weekday()
    return day_start - weekday * 86400


def event_currency(event: Any) -> str:
    """Events without a currency (older rows, owner-added) count as US news."""
    if isinstance(event, dict):
        return str(event.get("currency") or "USD").upper()
    return "USD"


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

    def is_trading_allowed(
        self,
        repository: Any,
        now_ts: int,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> tuple[bool, str]:
        now_ts = int(now_ts)

        if settings.BOT_PAUSED:
            return False, "Risk governor: paused by the owner (BOT_PAUSED switch)"

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

        # Correlated markets moving together are one bet, not two: never stack
        # same-direction exposure inside a correlation bloc (EURUSD/GBPUSD).
        if symbol and direction:
            try:
                group = get_instrument(symbol).correlation_group
                if group:
                    open_signals = repository.get_open_signals()
                    for open_signal in open_signals if isinstance(open_signals, list) else []:
                        open_symbol = getattr(open_signal, "symbol", None)
                        if not isinstance(open_symbol, str) or not open_symbol:
                            continue
                        if open_symbol.upper() == str(symbol).upper():
                            continue
                        if get_instrument(open_symbol).correlation_group != group:
                            continue
                        open_direction = str(
                            getattr(open_signal, "signal_type", "") or ""
                        ).upper()
                        if open_direction == str(direction).upper():
                            return False, (
                                f"Risk governor: correlated exposure blocked "
                                f"({open_symbol} already {open_direction}; "
                                f"{symbol} would double the same bet)"
                            )
            except Exception as exc:
                logging.debug("Risk governor correlation check skipped: %s", exc)

        dollar_reason = self._same_dollar_bet_reason(repository, symbol, direction)
        if dollar_reason:
            return False, dollar_reason

        if symbol and str(settings.WEEKEND_ACTION).lower() in {"close", "breakeven"}:
            remaining = minutes_to_weekly_close(symbol, now_ts)
            if remaining is not None and remaining <= WEEKEND_NO_NEW_MINUTES:
                return False, (
                    "Risk governor: the market closes for the weekend in "
                    f"{remaining} min; no new trades"
                )

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

        weekly_r = self._weekly_r(repository, now_ts)
        if weekly_r is not None and weekly_r <= -abs(float(settings.RISK_WEEKLY_MAX_LOSS_R)):
            return False, (
                f"Risk governor: weekly loss brake ({weekly_r:+.2f}R this week); "
                "new signals resume on Monday"
            )

        blackout_reason = self._news_blackout_reason(repository, now_ts, symbol)
        if blackout_reason:
            return False, blackout_reason

        return True, "Risk governor: trading allowed"

    @staticmethod
    def _weekly_r(repository: Any, now_ts: int) -> Optional[float]:
        try:
            results = repository.get_closed_results_since(week_start(now_ts))
        except Exception:
            return None
        if not isinstance(results, list):
            return None
        total = 0.0
        for row in results:
            try:
                total += float(row[2])
            except (TypeError, ValueError, IndexError):
                continue
        return round(total, 4)

    @staticmethod
    def _same_dollar_bet_reason(
        repository: Any, symbol: Optional[str], direction: Optional[str]
    ) -> Optional[str]:
        """Gold, EUR and GBP all move against the US dollar: buying any of them
        is partly the same bet. Cap how many run at once the same way."""
        if not symbol or not direction:
            return None
        try:
            exposure = get_instrument(symbol).usd_exposure
            if not exposure:
                return None
            wanted = exposure * (1 if str(direction).upper() == "LONG" else -1)
            open_signals = repository.get_open_signals()
            same = 0
            for open_signal in open_signals if isinstance(open_signals, list) else []:
                if getattr(open_signal, "trial", False):
                    continue
                open_symbol = getattr(open_signal, "symbol", None)
                if not isinstance(open_symbol, str) or not open_symbol:
                    continue
                open_exposure = get_instrument(open_symbol).usd_exposure
                if not open_exposure:
                    continue
                open_direction = str(getattr(open_signal, "signal_type", "") or "").upper()
                if open_exposure * (1 if open_direction == "LONG" else -1) == wanted:
                    same += 1
            limit = int(settings.RISK_MAX_SAME_USD_BET)
            if same >= limit:
                side = "against" if wanted < 0 else "on"
                return (
                    f"Risk governor: same dollar bet already open ({same} trades already bet "
                    f"{side} the US dollar; max {limit})"
                )
        except Exception as exc:
            logging.debug("Risk governor dollar check skipped: %s", exc)
        return None

    def _news_blackout_reason(
        self, repository: Any, now_ts: int, symbol: Optional[str] = None
    ) -> Optional[str]:
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

        currencies = set(get_instrument(symbol).news_currencies) if symbol else None
        for event in events:
            if currencies is not None and event_currency(event) not in currencies:
                continue
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
