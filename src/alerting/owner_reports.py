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
    return "\n".join(lines)


def run_detail(feed: dict[str, Any]) -> str:
    return json.dumps(feed, separators=(",", ":"))
