"""Messages for the owner's private chat (never the public channel)."""

from __future__ import annotations

import html
import json
import time
from typing import Any, Optional

from config.instruments import active_symbols, get_instrument, state_key

SOURCE_LABELS = {
    "SPOT": "spot feed (TwelveData)",
    "BACKUP": "BACKUP futures prices",
    "YAHOO": "Yahoo",
    "TWELVEDATA": "TwelveData (Yahoo down)",
    "NONE": "no prices (skipped)",
    "WAIT": "waiting for the next candle",
}


def _latest_feed(repository: Any, symbol: str) -> Optional[tuple[str, Optional[int], int]]:
    rows = repository.query(
        "SELECT source, lag_seconds, timestamp FROM feed_health "
        "WHERE symbol = ? ORDER BY id DESC LIMIT 1;",
        (symbol,),
    )
    if not rows:
        return None
    source, lag, ts = rows[0]
    return str(source), (None if lag is None else int(lag)), int(ts)


def build_daily_check(repository: Any, now: Optional[int] = None) -> str:
    now = int(now or time.time())
    day_ago = now - 86400

    runs, failing, slowest = repository.query(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN errors > 0 THEN 1 ELSE 0 END), 0), "
        "COALESCE(MAX(duration_ms), 0) FROM pulse_runs WHERE timestamp >= ?;",
        (day_ago,),
    )[0]
    signals_24h = repository.count_signals_since(day_ago)
    open_now = len(repository.get_open_signals())
    queued = len(repository.outbox_rows(["PENDING"], limit=500))
    uncertain = len(repository.outbox_rows(["UNCERTAIN"], limit=500))

    lines = [
        "\u2705 <b>Daily check</b>",
        f"Runs in the last 24h: <b>{int(runs)}</b> (about 288 expected)"
        + (f" | with errors: <b>{int(failing)}</b>" if failing else ""),
        f"Slowest run: {int(slowest) / 1000:.0f}s",
        f"Signals sent: <b>{signals_24h}</b> | open now: <b>{open_now}</b>",
    ]
    if queued or uncertain:
        lines.append(
            f"Telegram messages waiting: {queued}"
            + (f" | possibly missed: {uncertain}" if uncertain else "")
        )

    lines.append("")
    lines.append("<b>Prices</b>")
    for symbol in active_symbols():
        name = get_instrument(symbol).display_name
        feed = _latest_feed(repository, symbol)
        if feed is None:
            lines.append(f"\u2022 {name}: no data yet")
            continue
        source, lag, _ts = feed
        lag_text = "no new candle" if lag is None else f"{lag // 60} min behind"
        lines.append(f"\u2022 {name}: {SOURCE_LABELS.get(source, source)}, {lag_text}")

    setups = []
    for symbol in active_symbols():
        classification = repository.get_kv(state_key("latest_setup_classification", symbol))
        if classification:
            score = repository.get_kv(state_key("latest_setup_score", symbol))
            setups.append(
                f"{get_instrument(symbol).display_name} {html.escape(str(classification))}"
                + (f" ({score})" if score else "")
            )
    if setups:
        lines.append("")
        lines.append("Last ideas: " + " | ".join(setups))
    trials = trial_scorecard(repository)
    if trials:
        lines.append("")
        lines.append("<b>Trials</b> (owner only, after costs)")
        lines.extend(trials)
    return "\n".join(lines)


TRIAL_READY_TRADES = 30


def trial_scorecard(repository: Any) -> list[str]:
    """One line per trial market/strategy: trades, result after costs, and
    whether it has earned a place in the public channel."""
    from config import settings
    from src.analysis.evidence import cost_in_r, summarize
    from src.analysis.outcomes import row_r

    rows = repository.query(
        "SELECT symbol, COALESCE(strategy, 'UNKNOWN'), status, realized_r, "
        "COALESCE(entry_price, entry), COALESCE(sl_price, sl) FROM signals "
        "WHERE COALESCE(trial, 0) = 1 AND status LIKE 'CLOSED%';"
    )
    groups: dict[tuple[str, str], list[float]] = {}
    for symbol, strategy, status, realized, entry, sl in rows:
        value = row_r(status, realized)
        if value is None:
            continue
        cost = cost_in_r(str(symbol), entry, sl, bool(settings.SPREAD_CUSHION_ENABLED))
        groups.setdefault((str(symbol), str(strategy)), []).append(float(value) - cost)
    lines = []
    for (symbol, strategy), values in sorted(groups.items()):
        stats = summarize(values)
        ready = (
            stats["trades"] >= TRIAL_READY_TRADES
            and stats["net_r"] > 0
            and (stats.get("profit_factor") or 0) >= 1.1
        )
        lines.append(
            f"\u2022 {html.escape(get_instrument(symbol).display_name)} / "
            f"{html.escape(strategy.replace('_', ' ').title())}: {stats['trades']} trades, "
            f"{stats['net_r']:+.1f}R"
            + (" \u2705 ready to go public" if ready else "")
        )
    return lines


def run_detail(feed: dict[str, Any]) -> str:
    return json.dumps(feed, separators=(",", ":"))


def month_bounds(year: int, month: int) -> tuple[int, int]:
    from datetime import datetime, timezone

    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def build_monthly_risk_review(repository: Any, year: int, month: int) -> Optional[str]:
    """Worst day, worst losing streak and biggest loss against the plan (1R)."""
    from datetime import datetime, timezone

    from config import settings
    from src.analysis.evidence import load_evidence
    from src.analysis.luck_test import longest_losing_streak, max_drawdown
    from src.analysis.outcomes import row_r

    start, end = month_bounds(year, month)
    rows = repository.query(
        "SELECT symbol, strategy, status, realized_r, COALESCE(closed_at, timestamp) FROM signals "
        "WHERE status LIKE 'CLOSED%' AND COALESCE(trial, 0) = 0 "
        "AND COALESCE(closed_at, timestamp) >= ? AND COALESCE(closed_at, timestamp) < ? "
        "ORDER BY COALESCE(closed_at, timestamp), id;",
        (start, end),
    )
    trades = []
    for symbol, strategy, status, realized, closed in rows:
        value = row_r(status, realized)
        if value is not None:
            trades.append((str(symbol), str(strategy or "UNKNOWN"), float(value), int(closed)))
    label = datetime(year, month, 1, tzinfo=timezone.utc).strftime("%B %Y")
    if not trades:
        return f"\U0001f4cb <b>Risk review, {label}</b>\nNo finished trades this month."

    results = [t[2] for t in trades]
    by_day: dict[str, float] = {}
    for _symbol, _strategy, value, closed in trades:
        day = datetime.fromtimestamp(closed, tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0.0) + value
    worst_day, worst_day_r = min(by_day.items(), key=lambda item: item[1])
    limit_days = sum(1 for v in by_day.values() if v <= -abs(float(settings.RISK_DAILY_MAX_LOSS_R)))
    worst = min(trades, key=lambda t: t[2])
    streak = longest_losing_streak(results)
    dip = max_drawdown(results)

    luck = (load_evidence().get("luck") or {}).get("ALL") or {}
    lines = [
        f"\U0001f4cb <b>Risk review, {label}</b>",
        f"Finished trades: <b>{len(trades)}</b> | result: <b>{sum(results):+.2f}R</b>",
        f"Worst day: {worst_day} ({worst_day_r:+.2f}R)"
        + (f" | daily loss limit reached on {limit_days} day(s)" if limit_days else ""),
        f"Longest losing streak: {streak}"
        + (
            f" (history: up to {luck['bad_luck_losing_streak']} with bad luck)"
            if luck.get("bad_luck_losing_streak") is not None
            else ""
        ),
        f"Deepest dip: {dip:.2f}R"
        + (
            f" (history: up to {luck['bad_luck_max_drawdown_r']}R with bad luck)"
            if luck.get("bad_luck_max_drawdown_r") is not None
            else ""
        ),
    ]
    overshoot = -1.0 - worst[2]
    if overshoot > 0.05:
        lines.append(
            f"\u26a0\ufe0f Biggest loss: {worst[2]:+.2f}R on {html.escape(worst[0])} ({html.escape(worst[1])}), "
            f"{overshoot:.2f}R worse than the planned 1R (price jumped past the stop)."
        )
    else:
        lines.append(f"Biggest loss: {worst[2]:+.2f}R, within the planned 1R.")
    if luck.get("bad_luck_losing_streak") is not None and streak > int(luck["bad_luck_losing_streak"]):
        lines.append("\u26a0\ufe0f The losing streak is longer than history allows for bad luck: review before continuing.")
    return "\n".join(lines)
