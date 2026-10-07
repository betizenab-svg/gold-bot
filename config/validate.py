"""Check settings before the bot starts, with plain-English messages.

Reads raw environment values (it does NOT import config.settings), so a typo
like RISK_PER_TRADE_PCT=one gives a clear message instead of a crash.

    python -m config.validate
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

Rule = tuple[str, Callable[[str], object], Optional[float], Optional[float], str]

NUMBERS: list[Rule] = [
    ("RISK_PER_TRADE_PCT", float, 0.05, 5.0, "risk per trade, in % of the account"),
    ("RISK_DAILY_MAX_LOSS_R", float, 0.5, 20.0, "daily loss limit in R"),
    ("RISK_WEEKLY_MAX_LOSS_R", float, 1.0, 50.0, "weekly loss brake in R"),
    ("RISK_DAILY_PROFIT_LOCK_R", float, 0.5, 50.0, "daily profit lock in R"),
    ("RISK_MAX_SIGNALS_PER_DAY", int, 1, 50, "signals per day"),
    ("RISK_MAX_CONCURRENT_SIGNALS", int, 1, 20, "trades open at once"),
    ("RISK_MAX_SAME_USD_BET", int, 1, 20, "trades betting on the dollar the same way"),
    ("RISK_CONSECUTIVE_SL_HALT", int, 1, 20, "stops in a row before pausing"),
    ("TP1_R", float, 0.3, 10.0, "first target in R"),
    ("TP2_R", float, 0.5, 20.0, "second target in R"),
    ("SIGNAL_EXPIRY_MINUTES", int, 5, 10080, "minutes an order waits to fill"),
    ("NEWS_BLACKOUT_BEFORE_MIN", int, 0, 600, "minutes paused before news"),
    ("NEWS_BLACKOUT_AFTER_MIN", int, 0, 600, "minutes paused after news"),
    ("NEWS_WARN_MINUTES", int, 0, 600, "minutes of warning before news"),
    ("WEEKEND_EXIT_MINUTES", int, 0, 600, "minutes before the weekend close"),
    ("MORNING_BRIEFING_HOUR_UTC", int, 0, 23, "hour of the morning briefing (UTC)"),
    ("DAILY_STATUS_HOUR_UTC", int, 0, 23, "hour of the daily check (UTC)"),
    ("FREE_SIGNALS_PER_DAY", int, 0, 50, "signals copied to the free channel"),
]

CHOICES = {
    "ENTRY_MODE": {"limit", "market"},
    "WEEKEND_ACTION": {"close", "breakeven", "off"},
    "TREND_DAY_FILTER": {"block", "off"},
    "MESSAGE_LANGUAGE": {"en", "am", "both"},
}

CHAT_IDS = ["TELEGRAM_CHAT_ID", "TELEGRAM_ADMIN_CHAT_ID", "TELEGRAM_VIP_CHAT_ID"]


def check(env: Optional[dict] = None) -> list[str]:
    env = os.environ if env is None else env
    problems: list[str] = []
    for name, kind, low, high, meaning in NUMBERS:
        raw = str(env.get(name, "") or "").strip()
        if not raw:
            continue
        try:
            value = kind(raw)
        except ValueError:
            word = "whole number" if kind is int else "number"
            problems.append(f"{name}={raw!r} is not a {word} ({meaning}).")
            continue
        if (low is not None and value < low) or (high is not None and value > high):
            problems.append(f"{name}={raw} is outside the safe range {low}-{high} ({meaning}).")
    for name, allowed in CHOICES.items():
        raw = str(env.get(name, "") or "").strip().lower()
        if raw and raw not in allowed:
            problems.append(f"{name}={raw!r} must be one of: {', '.join(sorted(allowed))}.")
    for name in CHAT_IDS + ["PARTNER_CHAT_IDS"]:
        for part in str(env.get(name, "") or "").split(","):
            part = part.strip()
            if part and not (part.lstrip("-").isdigit() or part.startswith("@")):
                problems.append(f"{name} has {part!r}: a chat id is a number like -1001234567890 or @channelname.")
    tp1, tp2 = env.get("TP1_R"), env.get("TP2_R")
    try:
        if tp1 and tp2 and float(tp1) >= float(tp2):
            problems.append(f"TP1_R ({tp1}) must be smaller than TP2_R ({tp2}).")
    except ValueError:
        pass
    symbols = str(env.get("SYMBOLS", "") or "").strip()
    if symbols:
        try:
            from config.instruments import INSTRUMENTS

            unknown = [s for s in symbols.upper().split(",") if s.strip() and s.strip() not in INSTRUMENTS]
            if unknown:
                problems.append(f"SYMBOLS has unknown markets: {', '.join(unknown)}.")
        except Exception:
            pass
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("Settings problems found:")
        for line in problems:
            print(f" - {line}")
        return 1
    print("Settings look fine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
