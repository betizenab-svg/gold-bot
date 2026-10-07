"""Publish the public track record (run after every pulse).

* site/data/ledger.jsonl: append-only, one line per signal event (opened,
  finished, cancelled). Each line carries the SHA-256 of the line before it,
  and lines are never rewritten, so the git history proves nothing was
  changed afterwards.
* site/data/public.json: totals, monthly table, markets, strategies, running
  total, open signals, last 10 results and upcoming news. It is rewritten only
  when its content changes, so quiet runs make no commit.

Trial signals (owner only) never appear here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.instruments import INSTRUMENTS, get_instrument  # noqa: E402
from src.analysis.luck_test import longest_losing_streak, max_drawdown  # noqa: E402
from src.analysis.outcomes import row_r  # noqa: E402

SITE_DATA = ROOT_DIR / "site" / "data"
LEDGER_FIELDS = ("seq", "event", "code", "id", "symbol", "direction", "order", "entry", "stop",
                 "tp1", "tp2", "strategy", "signal_time", "event_time", "status", "result_r",
                 "code_version", "backfilled", "prev")


def _iso(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _code(symbol: str, signal_id: int) -> str:
    instrument = get_instrument(symbol)
    return f"#{instrument.code_prefix or instrument.symbol[:1]}{signal_id}"


def load_signals(db_path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    columns = {row[1] for row in connection.execute("PRAGMA table_info(signals);")}

    def col(name: str, fallback: str = "NULL") -> str:
        return name if name in columns else fallback

    rows = connection.execute(
        f"""
        SELECT id, symbol, COALESCE(signal_type, type) AS direction, {col('order_type')} AS order_type,
               COALESCE(entry_price, entry) AS entry, COALESCE(sl_price, sl) AS stop,
               COALESCE(tp1_price, tp1) AS tp1, COALESCE(tp2_price, tp2) AS tp2,
               {col('strategy')} AS strategy, timestamp, status, {col('realized_r')} AS realized_r,
               {col('closed_at')} AS closed_at, {col('code_version')} AS code_version,
               COALESCE({col('trial', '0')}, 0) AS trial
        FROM signals ORDER BY id;
        """
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows if not int(row["trial"] or 0)]


def ledger_events(signal: dict[str, Any]) -> list[dict[str, Any]]:
    base = {
        "code": _code(str(signal["symbol"]), int(signal["id"])),
        "id": int(signal["id"]),
        "symbol": str(signal["symbol"]),
        "direction": str(signal["direction"] or "").upper(),
        "order": str(signal["order_type"] or "LIMIT").upper(),
        "entry": signal["entry"], "stop": signal["stop"], "tp1": signal["tp1"], "tp2": signal["tp2"],
        "strategy": signal["strategy"],
        "signal_time": _iso(signal["timestamp"]),
        "code_version": signal["code_version"],
    }
    events = [dict(base, event="OPEN", event_time=_iso(signal["timestamp"]))]
    status = str(signal["status"] or "").upper()
    if status.startswith("CLOSED"):
        events.append(
            dict(base, event="CLOSE", status=status, result_r=row_r(status, signal["realized_r"]),
                 event_time=_iso(signal["closed_at"] or signal["timestamp"]))
        )
    elif status == "CANCELLED":
        events.append(dict(base, event="CANCEL", status=status,
                           event_time=_iso(signal["closed_at"] or signal["timestamp"])))
    return events


def update_ledger(path: Path, signals: list[dict[str, Any]]) -> int:
    """Append new events only; returns how many lines were added."""
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for line in existing:
        item = json.loads(line)
        seen.add((item["id"], item["event"]))
    backfill = not existing
    prev = hashlib.sha256(existing[-1].encode("utf-8")).hexdigest() if existing else "genesis"
    added: list[str] = []
    seq = len(existing)
    for signal in signals:
        for event in ledger_events(signal):
            if (event["id"], event["event"]) in seen:
                continue
            seq += 1
            event.update(seq=seq, prev=prev, backfilled=backfill)
            line = json.dumps({k: event.get(k) for k in LEDGER_FIELDS if k in event}, sort_keys=True,
                              separators=(",", ":"))
            added.append(line)
            prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
            seen.add((event["id"], event["event"]))
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(added) + "\n")
    return len(added)


def verify_ledger(path: Path) -> bool:
    """True when every line points at the hash of the line before it."""
    prev = "genesis"
    for line in path.read_text(encoding="utf-8").splitlines():
        if json.loads(line).get("prev") != prev:
            return False
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return True


def _summary(values: list[float]) -> dict[str, Any]:
    count = len(values)
    wins = sum(1 for v in values if v > 0)
    return {
        "trades": count,
        "wins": wins,
        "losses": sum(1 for v in values if v < 0),
        "flat": sum(1 for v in values if v == 0),
        "net_r": round(sum(values), 2),
        "win_rate": round(100.0 * wins / count, 1) if count else None,
        "worst_losing_streak": longest_losing_streak(values),
        "deepest_dip_r": round(max_drawdown(values), 2),
    }


def build_public(signals: list[dict[str, Any]], news: list[dict[str, Any]], now: int) -> dict[str, Any]:
    from config import settings
    from src.analysis.evidence import cost_in_r, load_evidence

    closed = []
    for signal in signals:
        status = str(signal["status"] or "").upper()
        value = row_r(status, signal["realized_r"]) if status.startswith("CLOSED") else None
        if value is not None:
            closed.append((int(signal["closed_at"] or signal["timestamp"]), signal, float(value)))
    closed.sort(key=lambda item: item[0])
    values = [v for _, _, v in closed]
    after_costs = [
        v - cost_in_r(str(s["symbol"]), s["entry"], s["stop"], bool(settings.SPREAD_CUSHION_ENABLED))
        for _, s, v in closed
    ]
    months: dict[str, list[float]] = {}
    markets: dict[str, list[float]] = {}
    strategies: dict[str, list[float]] = {}
    for ts, signal, value in closed:
        months.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m"), []).append(value)
        markets.setdefault(str(signal["symbol"]), []).append(value)
        strategies.setdefault(str(signal["strategy"] or "UNKNOWN"), []).append(value)
    running, equity = 0.0, []
    for ts, _signal, value in closed:
        running += value
        equity.append([ts, round(running, 2)])
    month_cutoff = now - 30 * 86400
    open_rows = [s for s in signals if str(s["status"] or "").upper() in {"PENDING", "ACTIVE", "PARTIAL_TP1"}]
    evidence = load_evidence()
    luck = (evidence.get("luck") or {}).get("ALL") or {}
    return {
        "totals": dict(_summary(values), net_r_after_costs=round(sum(after_costs), 2)),
        "history": {
            "window": evidence.get("window"),
            "bad_luck_losing_streak": luck.get("bad_luck_losing_streak"),
            "bad_luck_dip_r": luck.get("bad_luck_max_drawdown_r"),
            "markets": evidence.get("markets") or {},
        },
        "last_30_days": _summary([v for ts, _s, v in closed if ts >= month_cutoff]),
        "months": [dict(_summary(v), month=m) for m, v in sorted(months.items())],
        "markets": {s: dict(_summary(v), name=get_instrument(s).display_name) for s, v in sorted(markets.items())},
        "strategies": {s: _summary(v) for s, v in sorted(strategies.items())},
        "equity": equity,
        "open": [
            {
                "code": _code(str(s["symbol"]), int(s["id"])), "symbol": s["symbol"],
                "name": get_instrument(s["symbol"]).display_name, "direction": s["direction"],
                "order": s["order_type"], "entry": s["entry"], "stop": s["stop"], "tp1": s["tp1"],
                "tp2": s["tp2"], "status": s["status"], "time": int(s["timestamp"]),
            }
            for s in open_rows
        ],
        "last10": [
            {
                "code": _code(str(s["symbol"]), int(s["id"])), "symbol": s["symbol"],
                "name": get_instrument(s["symbol"]).display_name, "direction": s["direction"],
                "status": s["status"], "result_r": round(v, 2), "closed_at": ts,
            }
            for ts, s, v in reversed(closed[-10:])
        ],
        "news": news,
        "instruments": {
            symbol: {
                "name": i.display_name, "pip_size": i.pip_size, "pip_value_per_lot": i.pip_value_per_lot,
                "decimals": i.price_decimals,
            }
            for symbol, i in INSTRUMENTS.items() if not i.trial
        },
    }


def upcoming_news(db_path: Path, now: int) -> list[dict[str, Any]]:
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = connection.execute(
            "SELECT value FROM kv_store WHERE key = 'upcoming_news_events_json';"
        ).fetchone()
        connection.close()
        events = json.loads(row[0]) if row and row[0] else []
    except (sqlite3.Error, ValueError):
        return []
    return [
        {"time": int(e["timestamp"]), "label": str(e.get("label") or "news"),
         "currency": str(e.get("currency") or "USD")}
        for e in events
        if isinstance(e, dict) and now - 3600 < int(e.get("timestamp", 0) or 0) < now + 7 * 86400
    ][:40]


def write_public(path: Path, payload: dict[str, Any], now: int) -> bool:
    """Rewrite only when something other than the time stamp changed."""
    body = json.dumps(payload, sort_keys=True)
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            old.pop("updated", None)
            if json.dumps(old, sort_keys=True) == body:
                return False
        except ValueError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload, updated=_iso(now)), sort_keys=True, indent=1) + "\n",
                    encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT_DIR / "data" / "trading_engine.db"))
    parser.add_argument("--out", default=str(SITE_DATA))
    args = parser.parse_args(argv)
    db_path, out = Path(args.db), Path(args.out)
    if not db_path.exists():
        print("No database; nothing to publish.")
        return 0
    now = int(time.time())
    signals = load_signals(db_path)
    added = update_ledger(out / "ledger.jsonl", signals)
    changed = write_public(out / "public.json", build_public(signals, upcoming_news(db_path, now), now), now)
    print(f"ledger: +{added} line(s); public.json {'updated' if changed else 'unchanged'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
