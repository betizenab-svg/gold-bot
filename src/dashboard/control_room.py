"""Owner control room: plain numbers computed straight from the bot's database.

Every function takes an open sqlite3 connection (row_factory = sqlite3.Row)
and returns plain dicts/lists so the page and the tests share one source.
"""

from __future__ import annotations

import math
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Optional

from src.analysis.evidence import baseline, load_evidence, summarize
from src.analysis.outcomes import row_r

EAT_OFFSET = 3 * 3600


def _rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    except sqlite3.Error:
        return []


def closed_trades(conn: sqlite3.Connection, include_trial: bool = False) -> list[dict[str, Any]]:
    trial_clause = "" if include_trial else "AND COALESCE(trial, 0) = 0"
    rows = _rows(
        conn,
        f"""
        SELECT symbol, COALESCE(strategy, 'UNKNOWN') AS strategy, status, realized_r,
               timestamp, COALESCE(closed_at, timestamp) AS closed_at,
               COALESCE(trial, 0) AS trial
        FROM signals WHERE status LIKE 'CLOSED%' {trial_clause}
        ORDER BY COALESCE(closed_at, timestamp) ASC, id ASC;
        """,
    )
    for row in rows:
        row["r"] = float(row_r(row.get("status"), row.get("realized_r")) or 0.0)
    return rows


def running_total(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Running total plus the dips (distance below the best point so far)."""
    total = peak = 0.0
    totals: list[float] = []
    dips: list[float] = []
    for trade in trades:
        total += trade["r"]
        peak = max(peak, total)
        totals.append(round(total, 2))
        dips.append(round(total - peak, 2))
    return {
        "totals": totals,
        "dips": dips,
        "worst_dip": min(dips, default=0.0),
        "current_dip": dips[-1] if dips else 0.0,
    }


def _group(trades: list[dict[str, Any]], key) -> list[dict[str, Any]]:
    groups: dict[Any, list[float]] = {}
    for trade in trades:
        groups.setdefault(key(trade), []).append(trade["r"])
    output = []
    for name, values in groups.items():
        wins = sum(1 for v in values if v > 0)
        output.append(
            {
                "name": name,
                "trades": len(values),
                "win_pct": round(100.0 * wins / len(values), 1),
                "net_r": round(sum(values), 2),
            }
        )
    return output


def by_market(trades):
    return sorted(_group(trades, lambda t: str(t.get("symbol") or "?")), key=lambda g: -g["net_r"])


def by_strategy(trades):
    return sorted(_group(trades, lambda t: str(t.get("strategy"))), key=lambda g: -g["net_r"])


def by_hour(trades):
    """Grouped by the Ethiopian (EAT) hour the idea was sent."""
    groups = _group(
        trades,
        lambda t: datetime.fromtimestamp(int(t.get("timestamp") or 0) + EAT_OFFSET, tz=timezone.utc).hour,
    )
    return sorted(groups, key=lambda g: g["name"])


def idea_funnel(conn: sqlite3.Connection, since: int) -> dict[str, Any]:
    """How many ideas were spotted, why they stopped, and how many were sent."""
    from scripts.history.report import reason_category

    rows = _rows(
        conn,
        "SELECT classification, vetoes FROM setup_log WHERE timestamp >= ?;",
        (since,),
    )
    stages: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for row in rows:
        label = str(row.get("classification") or "UNKNOWN").upper()
        stages[label] = stages.get(label, 0) + 1
        if label in {"REJECTED", "BLOCKED"}:
            reason = reason_category(label, str(row.get("vetoes") or ""))
            reasons[reason] = reasons.get(reason, 0) + 1
    sent = _rows(
        conn,
        "SELECT COUNT(*) AS n FROM signals WHERE timestamp >= ? AND COALESCE(trial,0)=0;",
        (since,),
    )
    return {
        "spotted": len(rows),
        "stages": sorted(stages.items(), key=lambda kv: -kv[1]),
        "reasons": sorted(reasons.items(), key=lambda kv: -kv[1]),
        "sent": int(sent[0]["n"]) if sent else 0,
    }


def filter_report_card(conn: sqlite3.Connection, since: int = 0) -> list[dict[str, Any]]:
    """For each filter: what the ideas it stopped would have made.

    A negative total means the filter saved money; positive means it cost us.
    """
    from scripts.history.report import reason_category

    rows = _rows(
        conn,
        """
        SELECT classification, vetoes, shadow_r FROM setup_log
        WHERE shadow_r IS NOT NULL AND timestamp >= ?;
        """,
        (since,),
    )
    groups: dict[str, list[float]] = {}
    for row in rows:
        reason = reason_category(str(row.get("classification") or ""), str(row.get("vetoes") or ""))
        groups.setdefault(reason, []).append(float(row["shadow_r"]))
    card = []
    for reason, values in groups.items():
        total = round(sum(values), 2)
        card.append(
            {
                "filter": reason,
                "stopped": len(values),
                "would_have_made_r": total,
                "verdict": "saved money" if total < 0 else ("cost us" if total > 0 else "no difference"),
            }
        )
    return sorted(card, key=lambda c: c["would_have_made_r"])


def traffic_lights(
    trades: list[dict[str, Any]], evidence: Optional[dict] = None, window: int = 30
) -> list[dict[str, Any]]:
    """Green/amber/red per market+strategy: recent trades vs the history baseline."""
    data = load_evidence() if evidence is None else evidence
    pairs: dict[tuple[str, str], list[float]] = {}
    for trade in trades:
        pairs.setdefault((str(trade.get("symbol")), str(trade.get("strategy"))), []).append(trade["r"])
    lights = []
    for (symbol, strategy), values in sorted(pairs.items()):
        recent = values[-window:]
        stats = summarize(recent)
        base = baseline(symbol, strategy, data) or {}
        expected = base.get("expectancy_r")
        light, note = "grey", "no history baseline yet"
        if expected is not None and len(recent) >= 5:
            std = float(base.get("std_r") or stats.get("std_r") or 1.0) or 1.0
            error = std / math.sqrt(len(recent))
            gap = stats["expectancy_r"] - float(expected)
            if gap >= -error:
                light, note = "green", "behaving like its history"
            elif gap >= -2 * error:
                light, note = "amber", "a bit worse than its history - watch it"
            else:
                light, note = "red", "much worse than its history - consider switching off"
        elif len(recent) < 5:
            note = "fewer than 5 trades - too early to judge"
        lights.append(
            {
                "symbol": symbol,
                "strategy": strategy,
                "recent_trades": len(recent),
                "recent_avg_r": stats.get("expectancy_r", 0.0),
                "history_avg_r": expected,
                "light": light,
                "note": note,
            }
        )
    return lights


def price_sources(conn: sqlite3.Connection, since: int) -> list[dict[str, Any]]:
    """How each price source behaved: typical delay, failures, and how often used."""
    rows = _rows(
        conn,
        """
        SELECT COALESCE(source, '?') AS source, COUNT(*) AS checks,
               SUM(CASE WHEN ok = 1 THEN 0 ELSE 1 END) AS failures,
               ROUND(AVG(CASE WHEN ok = 1 THEN lag_seconds END), 0) AS avg_lag,
               MAX(CASE WHEN ok = 1 THEN lag_seconds END) AS worst_lag,
               MAX(timestamp) AS last_seen
        FROM feed_health WHERE timestamp >= ?
        GROUP BY COALESCE(source, '?') ORDER BY checks DESC;
        """,
        (since,),
    )
    total = sum(int(r["checks"] or 0) for r in rows) or 1
    for row in rows:
        row["share_pct"] = round(100.0 * int(row["checks"] or 0) / total, 1)
        row["failures"] = int(row["failures"] or 0)
    return rows


def build(conn: sqlite3.Connection, days: int = 30, now: Optional[int] = None) -> dict[str, Any]:
    now = int(now or time.time())
    since = now - days * 86400
    trades = closed_trades(conn)
    return {
        "days": days,
        "curve": running_total(trades),
        "markets": by_market(trades),
        "strategies": by_strategy(trades),
        "hours": by_hour(trades),
        "funnel": idea_funnel(conn, since),
        "filters": filter_report_card(conn, since),
        "lights": traffic_lights(trades),
        "trial_lights": traffic_lights(closed_trades(conn, include_trial=True)),
        "sources": price_sources(conn, since),
    }
