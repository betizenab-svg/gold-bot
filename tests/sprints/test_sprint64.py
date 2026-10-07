"""Milestone 9: owner control room (items 87-92)."""

from __future__ import annotations

import sqlite3

from src.dashboard import control_room as room
from src.persistence.schema import SchemaInitializer

NOW = 1_760_000_000


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    SchemaInitializer(conn).initialize()
    conn.row_factory = sqlite3.Row
    return conn


def _signal(conn, symbol, strategy, r, ts, trial=0):
    conn.execute(
        "INSERT INTO signals (symbol, strategy, status, realized_r, timestamp, closed_at, trial, signal_hash)"
        " VALUES (?,?,?,?,?,?,?,?);",
        (symbol, strategy, "CLOSED_TP2" if r > 0 else "CLOSED_SL", r, ts, ts + 60, trial, f"h{ts}{symbol}"),
    )


def test_running_total_and_dips():
    trades = [{"r": v} for v in (1.0, 2.0, -1.0, -1.0, 0.5)]
    curve = room.running_total(trades)
    assert curve["totals"] == [1.0, 3.0, 2.0, 1.0, 1.5]
    assert curve["worst_dip"] == -2.0
    assert curve["current_dip"] == -1.5


def test_groups_and_trial_excluded():
    conn = _db()
    _signal(conn, "XAUUSD", "SWING", 2.0, NOW - 3600 * 5)
    _signal(conn, "XAUUSD", "SWING", -1.0, NOW - 3600 * 4)
    _signal(conn, "EURUSD", "ORB", 1.0, NOW - 3600 * 3, trial=1)
    trades = room.closed_trades(conn)
    assert len(trades) == 2
    market = room.by_market(trades)[0]
    assert market["name"] == "XAUUSD" and market["win_pct"] == 50.0 and market["net_r"] == 1.0
    assert len(room.closed_trades(conn, include_trial=True)) == 3
    assert all(0 <= h["name"] <= 23 for h in room.by_hour(trades))


def test_funnel_and_filter_card():
    conn = _db()
    rows = [
        ("REJECTED", "RSI 61: overbought", -1.0),
        ("REJECTED", "RSI 53: weak", -1.0),
        ("BLOCKED", "news pause", 2.0),
        ("A", "", None),
    ]
    for cls, vetoes, shadow in rows:
        conn.execute(
            "INSERT INTO setup_log (symbol, strategy, classification, vetoes, timestamp, shadow_r)"
            " VALUES ('XAUUSD','S',?,?,?,?);",
            (cls, vetoes, NOW - 100, shadow),
        )
    funnel = room.idea_funnel(conn, NOW - 86400)
    assert funnel["spotted"] == 4
    assert dict(funnel["stages"])["REJECTED"] == 2
    card = {c["filter"]: c for c in room.filter_report_card(conn)}
    assert card["News pause"]["verdict"] == "cost us"
    rsi = [c for c in card.values() if "RSI" in c["filter"]]
    assert len(rsi) == 1 and rsi[0]["stopped"] == 2 and rsi[0]["verdict"] == "saved money"


def test_traffic_lights():
    evidence = {"baselines": {"XAUUSD|SWING": {"expectancy_r": 0.2, "std_r": 1.0}}}
    good = [{"symbol": "XAUUSD", "strategy": "SWING", "r": 0.3} for _ in range(30)]
    bad = [{"symbol": "XAUUSD", "strategy": "SWING", "r": -1.0} for _ in range(30)]
    assert room.traffic_lights(good, evidence)[0]["light"] == "green"
    assert room.traffic_lights(bad, evidence)[0]["light"] == "red"
    other = [{"symbol": "EURUSD", "strategy": "X", "r": 1.0}] * 10
    assert room.traffic_lights(other, evidence)[0]["light"] == "grey"


def test_price_sources_and_build():
    conn = _db()
    for source, lag, ok in (("twelvedata", 30, 1), ("twelvedata", 90, 1), ("yahoo", 0, 0)):
        conn.execute(
            "INSERT INTO feed_health (symbol, source, lag_seconds, ok, timestamp) VALUES ('XAUUSD',?,?,?,?);",
            (source, lag, ok, NOW - 50),
        )
    sources = {s["source"]: s for s in room.price_sources(conn, NOW - 3600)}
    assert sources["twelvedata"]["avg_lag"] == 60 and sources["twelvedata"]["worst_lag"] == 90
    assert sources["yahoo"]["failures"] == 1
    data = room.build(conn, days=7, now=NOW)
    assert set(data) >= {"curve", "funnel", "filters", "lights", "sources"}


def test_control_room_script_builds_the_dashboard_on_a_private_copy(tmp_path, monkeypatch):
    import importlib

    from scripts import control_room

    module = importlib.import_module("src.dashboard.app")
    monkeypatch.setattr(module, "DB_PATH", module.DB_PATH)  # restored after the test
    monkeypatch.setenv("DASHBOARD_USERNAME", "owner")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    copy = tmp_path / "control_room.db"
    app = control_room.build_app(copy)
    app.config["LOGIN_DISABLED"] = True
    assert module.DB_PATH == str(copy)
    response = app.test_client().get("/control")
    assert response.status_code == 200 and b"Traffic lights" in response.data
