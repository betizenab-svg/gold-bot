"""Milestone 11: careful smart features (items 98-100)."""

from __future__ import annotations

import random
import sqlite3

from src.analysis import second_opinion as so
from src.analysis.market_mood import mood, mood_block_reason
from src.domain.candle import Candle


def _candles(moves, size=1.0):
    price, out = 100.0, []
    for i, move in enumerate(moves):
        new = price + move
        out.append(Candle("XAUUSD", "M5", i * 300, price, max(price, new) + size, min(price, new) - size, new, 0))
        price = new
    return out


def test_mood_labels():
    assert mood(_candles([0.5] * 120, size=0.2)) == "trending"
    assert mood(_candles([1, -1] * 60, size=0.2)) == "ranging"
    wild = _candles([1, -1] * 60, size=0.2) + _candles([15, -15] * 8, size=5)
    assert mood(wild) == "wild" and "wild" in mood_block_reason(wild)
    assert mood(_candles([1, 2])) == "unknown"


def _examples(n, signal=True):
    rng = random.Random(7)
    rows = []
    for i in range(n):
        score = rng.randint(40, 100)
        win = (score > 70) if signal else rng.random() < 0.5
        rows.append({"symbol": "XAUUSD", "strategy": "S", "direction": "LONG", "score": score,
                     "timestamp": 1_700_000_000 + i * 3600, "r": 1.5 if win else -1.0})
    return rows


def test_not_ready_below_minimum():
    model = so.build_model(_examples(100))
    assert model["ready"] is False and "500" in model["why"]
    assert so.veto_reason({"score": 10}, model) is None


def test_learns_real_pattern_and_vetoes():
    model = so.build_model(_examples(600))
    assert model["ready"] and model["helps"]
    assert model["walk_forward"]["avg_kept"] > model["walk_forward"]["avg_all"]
    low = {"symbol": "XAUUSD", "strategy": "S", "direction": "LONG", "score": 45, "timestamp": 1_700_000_000}
    high = dict(low, score=98)
    assert so.veto_reason(low, model) and so.veto_reason(high, model) is None


def test_noise_is_not_trusted():
    model = so.build_model(_examples(600, signal=False))
    if not model["helps"]:
        assert so.veto_reason({"symbol": "XAUUSD", "strategy": "S", "score": 0, "timestamp": 0}, model) is None


def test_load_examples_from_database():
    conn = sqlite3.connect(":memory:")
    from src.persistence.schema import SchemaInitializer

    SchemaInitializer(conn).initialize()
    conn.execute("INSERT INTO setup_log (symbol, strategy, direction, score, timestamp, shadow_r) VALUES ('XAUUSD','S','LONG',60,5,-1.0);")
    conn.execute("INSERT INTO signals (symbol, strategy, type, score, timestamp, status, realized_r, signal_hash) VALUES ('XAUUSD','S','SHORT',80,3,'CLOSED_TP2',2.0,'h');")
    rows = so.load_examples(conn)
    assert [r["timestamp"] for r in rows] == [3, 5] and rows[0]["direction"] == "SHORT"


def test_off_by_default():
    from config import settings

    assert settings.MOOD_FILTER == "off" and settings.SECOND_OPINION_ENABLED is False
