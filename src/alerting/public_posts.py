"""Posts for the public channel besides signals: the morning briefing, pause
notices, the weekly results card and the weekly lesson."""

from __future__ import annotations

import html
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config.instruments import active_symbols, get_instrument, is_trial, state_key
from src.alerting.i18n import t
from src.alerting.timefmt import eat, eat_date, eat_time

LESSONS = [
    "Risk the same small amount on every trade (about 1% of your account). Then no single trade, "
    "and no losing streak, can sink you.",
    "Losing streaks are normal. Even a method that works has runs of losses; plan for them before "
    "they happen, not during.",
    "Never move your stop further away. You set it when you were calm; trust that decision.",
    "No signal is also a signal. When the market is messy the bot waits, and so should you.",
    "Do not open extra trades to win back a loss. That is how small losses become big ones.",
    "Big news (like US inflation data) can make the price jump past a stop. Be careful holding "
    "trades into it.",
    "Use the lot sizes on each signal. If even the smallest lot (0.01) is too big for your "
    "balance, skip the trade.",
    "Count results in R, not money: +1R means you made what you risked, -1R means you lost it.",
    "Follow the whole plan: close half at target 1, move the stop to entry, let the rest run.",
    "A demo account is free. Practise there until following the signals feels boring.",
    "Do not enter late. If the price has already run far past the entry, let that trade go.",
    "Your broker's spread is a cost on every trade. Choose an account with low spreads on gold.",
    "Write down every trade: the date, the result in R and how you felt. Patterns will show up.",
    "Markets often jump when they reopen after the weekend; that is why trades are closed on Friday.",
    "Judge any method on about 100 trades, not on 5. A few results tell you almost nothing.",
    "Only trade money you can afford to lose. Paying bills must never depend on a trade.",
]


def lesson_for_week(iso_week: int) -> str:
    return LESSONS[(int(iso_week) - 1) % len(LESSONS)]


# --- Morning briefing ------------------------------------------------------------------

def _previous_day_levels(repository: Any, symbol: str, now: int) -> Optional[dict[str, float]]:
    """High, low and close of the last full UTC trading day, plus classic pivots."""
    day_start = now - now % 86400
    for back in range(1, 5):  # Monday looks back to Friday
        start = day_start - back * 86400
        rows = repository.query(
            "SELECT high, low, close FROM market_data WHERE symbol = ? AND timestamp >= ? "
            "AND timestamp < ? ORDER BY timestamp;",
            (symbol, start, start + 86400),
        )
        if len(rows) >= 12:
            high = max(float(r[0]) for r in rows)
            low = min(float(r[1]) for r in rows)
            close = float(rows[-1][2])
            pivot = (high + low + close) / 3.0
            return {
                "high": high, "low": low, "close": close, "pivot": pivot,
                "r1": 2 * pivot - low, "s1": 2 * pivot - high,
            }
    return None


def _public_currencies() -> set[str]:
    currencies: set[str] = set()
    for symbol in active_symbols():
        if not is_trial(symbol):
            currencies.update(get_instrument(symbol).news_currencies)
    return currencies or {"USD"}


def _events(repository: Any) -> list[dict[str, Any]]:
    try:
        raw = repository.get_kv("upcoming_news_events_json")
        events = json.loads(raw) if isinstance(raw, str) and raw else []
    except (TypeError, ValueError):
        return []
    return [e for e in events if isinstance(e, dict)]


def build_morning_briefing(repository: Any, now: int) -> str:
    from src.analysis.risk_governor import event_currency

    lines = [f"\u2600\ufe0f <b>{t('good_morning')}</b> \u00b7 {eat_date(now)}"]
    levels = _previous_day_levels(repository, "XAUUSD", now)
    nd = get_instrument("XAUUSD").price_decimals
    if levels:
        lines += [
            "",
            f"<b>{t('key_levels')}</b>",
            f"Yesterday: high <code>{levels['high']:.{nd}f}</code> \u00b7 low <code>{levels['low']:.{nd}f}</code>"
            f" \u00b7 close <code>{levels['close']:.{nd}f}</code>",
            f"Pivot <code>{levels['pivot']:.{nd}f}</code> \u00b7 above: <code>{levels['r1']:.{nd}f}</code>"
            f" \u00b7 below: <code>{levels['s1']:.{nd}f}</code>",
        ]
    local_day = eat(now).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start, day_end = int(local_day.timestamp()), int((local_day + timedelta(days=1)).timestamp())
    currencies = _public_currencies()
    todays = sorted(
        (e for e in _events(repository)
         if day_start <= int(e.get("timestamp", 0)) < day_end and event_currency(e) in currencies),
        key=lambda e: int(e["timestamp"]),
    )
    lines += ["", f"<b>{t('todays_news')}</b> (EAT)"]
    if todays:
        for event in todays[:8]:
            lines.append(
                f"\u2022 {eat_time(int(event['timestamp'])).replace(' EAT', '')} "
                f"{html.escape(str(event.get('label') or 'news'))} ({event_currency(event)})"
            )
        lines.append("New signals pause 30 minutes before and 15 minutes after each one.")
    else:
        lines.append(t("no_news"))
    structure = str(repository.get_kv(state_key("current_structure_state", "XAUUSD")) or "").upper()
    leaning = {
        "BULLISH": "buyers in control on gold's chart",
        "BEARISH": "sellers in control on gold's chart",
    }.get(structure, "no clear side yet on gold's chart")
    lines += ["", f"<b>{t('leaning')}:</b> {leaning}.",
              "<i>Signals come only when a setup is ready. Risk about 1% per trade.</i>"]
    return "\n".join(lines)


# --- Pause notices ---------------------------------------------------------------------

def news_pause_notice(event: dict[str, Any], before_min: int, after_min: int) -> str:
    from src.analysis.risk_governor import event_currency

    event_ts = int(event["timestamp"])
    return (
        f"\u23f8\ufe0f <b>{t('no_new_trades')}</b>: {html.escape(str(event.get('label') or 'big news'))} "
        f"({event_currency(event)}) at {eat_time(event_ts)}.\n"
        f"New signals resume after {eat_time(event_ts + after_min * 60)}. "
        "Holding a trade? Prices can jump: consider moving your stop to entry."
    )


BRAKE_NOTICES = {
    "daily loss limit": "\u23f8\ufe0f <b>No more signals today</b>: the daily loss limit was reached. "
    "Back tomorrow. Protecting the account comes first.",
    "weekly loss brake": "\u23f8\ufe0f <b>No more signals this week</b>: the weekly loss limit was "
    "reached. Back on Monday.",
    "paused by the owner": "\u23f8\ufe0f <b>Signals are paused for now.</b> Open trades are still "
    "followed and updated.",
}


def brake_notice(reason: str) -> Optional[tuple[str, str]]:
    lowered = str(reason).lower()
    for marker, text in BRAKE_NOTICES.items():
        if marker in lowered:
            return marker, text
    return None


# --- Weekly results card -------------------------------------------------------------------

def weekly_results(repository: Any, week_start: int) -> dict[str, Any]:
    from src.analysis.outcomes import row_r

    rows = repository.query(
        "SELECT symbol, status, realized_r, COALESCE(closed_at, timestamp) FROM signals "
        "WHERE status LIKE 'CLOSED%' AND COALESCE(trial, 0) = 0 "
        "AND COALESCE(closed_at, timestamp) >= ? AND COALESCE(closed_at, timestamp) < ? "
        "ORDER BY COALESCE(closed_at, timestamp), id;",
        (int(week_start), int(week_start) + 7 * 86400),
    )
    trades = []
    for symbol, status, realized, closed in rows:
        value = row_r(status, realized)
        if value is not None:
            trades.append((str(symbol), float(value), int(closed)))
    markets: dict[str, float] = {}
    for symbol, value, _ in trades:
        markets[symbol] = markets.get(symbol, 0.0) + value
    values = [v for _, v, _ in trades]
    return {
        "week_start": int(week_start),
        "trades": len(values),
        "wins": sum(1 for v in values if v > 0),
        "losses": sum(1 for v in values if v < 0),
        "flat": sum(1 for v in values if v == 0),
        "net_r": round(sum(values), 2),
        "markets": {k: round(v, 2) for k, v in sorted(markets.items())},
        "curve": [round(sum(values[: i + 1]), 2) for i in range(len(values))],
    }


def weekly_caption(stats: dict[str, Any]) -> str:
    start = stats["week_start"]
    label = f"{eat_date(start)} \u2013 {eat_date(start + 4 * 86400)}"
    if not stats["trades"]:
        return f"\U0001f4ca <b>{t('weekly_results')}</b> \u00b7 {label}\nNo finished trades this week."
    lines = [
        f"\U0001f4ca <b>{t('weekly_results')}</b> \u00b7 {label}",
        f"<b>{stats['net_r']:+.2f}R</b> from {stats['trades']} trades: {stats['wins']} won, "
        f"{stats['losses']} lost, {stats['flat']} closed at entry.",
    ]
    for symbol, value in stats["markets"].items():
        lines.append(f"\u2022 {html.escape(get_instrument(symbol).display_name)}: {value:+.2f}R")
    lines.append(f"<i>{t('losses_included')} 1R = the amount risked on one trade.</i>")
    return "\n".join(lines)


def render_weekly_card(stats: dict[str, Any], size: tuple[int, int] = (1080, 1080)) -> bytes:
    """Square results image (losses included) for Telegram and social posts."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    width, height = size
    figure = plt.figure(figsize=(width / 100, height / 100), dpi=100, facecolor="#0f172a")
    start = stats["week_start"]
    title = f"Weekly results \u00b7 {eat_date(start)} \u2013 {eat_date(start + 4 * 86400)}"
    figure.text(0.5, 0.92, title, ha="center", color="#e2e8f0", fontsize=26, weight="bold")
    net = stats["net_r"]
    figure.text(0.5, 0.78, f"{net:+.2f}R", ha="center", color="#22c55e" if net >= 0 else "#ef4444",
                fontsize=72, weight="bold")
    figure.text(
        0.5, 0.71,
        f"{stats['trades']} trades \u00b7 {stats['wins']} won \u00b7 {stats['losses']} lost \u00b7 "
        f"{stats['flat']} at entry",
        ha="center", color="#cbd5e1", fontsize=22,
    )
    axis = figure.add_axes([0.1, 0.22, 0.8, 0.42], facecolor="#0f172a")
    curve = [0.0] + list(stats["curve"])
    axis.plot(range(len(curve)), curve, color="#38bdf8", linewidth=4)
    axis.axhline(0, color="#475569", linewidth=1)
    axis.set_ylabel("Running total (R)", color="#cbd5e1", fontsize=16)
    axis.set_xlabel("Trades in order", color="#cbd5e1", fontsize=16)
    axis.tick_params(colors="#94a3b8", labelsize=13)
    for spine in axis.spines.values():
        spine.set_color("#334155")
    markets = " \u00b7 ".join(
        f"{get_instrument(s).display_name} {v:+.2f}R" for s, v in stats["markets"].items()
    ) or "No finished trades"
    figure.text(0.5, 0.12, markets, ha="center", color="#e2e8f0", fontsize=18)
    figure.text(0.5, 0.05, "Every trade counted, losses included. 1R = the amount risked per trade.",
                ha="center", color="#94a3b8", fontsize=15)
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    plt.close(figure)
    return buffer.getvalue()


def results_week_start(now: int) -> int:
    """Monday 00:00 UTC of the trading week to report: the one just finished on
    a weekend, otherwise the week before."""
    moment = datetime.fromtimestamp(int(now), tz=timezone.utc)
    this_monday = (moment - timedelta(days=moment.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if moment.weekday() < 5:
        this_monday -= timedelta(days=7)
    return int(this_monday.timestamp())
