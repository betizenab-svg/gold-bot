"""Sprint 53: performance guards — one DB connection per repository, linear
backtests, bounded log tails, and no repeated proxy sweeps."""

import random
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import requests

import src.ingestion.proxy_http as proxy_http
from src.backtest.engine import BacktestEngine
from src.dashboard.app import _read_last_lines
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _candles(count: int, seed: int = 11) -> list[Candle]:
    rng = random.Random(seed)
    price = 2000.0
    output = []
    for index in range(count):
        open_ = price
        close = open_ + rng.gauss(0, 1.2)
        high = max(open_, close) + abs(rng.gauss(0, 0.6))
        low = min(open_, close) - abs(rng.gauss(0, 0.6))
        output.append(
            Candle("XAUUSD", "M5", 1_700_000_000 + 300 * index, open_, high, low, close, 100.0)
        )
        price = close
    return output


def test_file_repository_reuses_one_connection(tmp_path: Path, monkeypatch) -> None:
    connection = sqlite3.connect(str(tmp_path / "sprint53.db"))
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)

    real_connect = sqlite3.connect
    opened: list[str] = []

    def counting_connect(*args, **kwargs):
        opened.append(str(args[0]) if args else "")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", counting_connect)

    repository.set_kv("k", "v")
    repository.save_candles(_candles(5))
    assert repository.get_kv("k") == "v"
    assert len(repository.get_recent_candles("XAUUSD", "M5", 10)) == 5
    assert opened == [], "queries must reuse the repository's connection"

    # A closed file-backed repository reopens lazily and still sees its data.
    repository.close()
    assert repository.get_kv("k") == "v"
    assert len(opened) == 1
    repository.close()


def test_backtest_bounded_window_matches_full_history() -> None:
    candles = _candles(2500)

    bounded = BacktestEngine(candles)
    assert bounded.lookback is not None
    bounded.run_simulation()

    full = BacktestEngine(candles)
    full.lookback = None  # legacy behavior: whole history per bar
    full.run_simulation()

    def outcome(engine: BacktestEngine) -> list[tuple]:
        return [
            (trade.signal.signal_hash, trade.status, trade.realized_pnl_usd, trade.closed_timestamp)
            for trade in engine.trade_history
        ]

    assert outcome(bounded), "synthetic data should produce trades"
    assert outcome(bounded) == outcome(full)
    assert bounded.current_balance == full.current_balance


def test_read_last_lines_matches_full_read(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    lines = [f'{{"pulse": {index}, "note": "é€ {"x" * (index % 300)}"}}' for index in range(3000)]
    path.write_text("\r\n".join(lines) + "\n", encoding="utf-8")

    assert _read_last_lines(path, line_count=100) == lines[-100:]
    assert _read_last_lines(path, line_count=5000) == lines

    path.write_text("", encoding="utf-8")
    assert _read_last_lines(path, line_count=100) == []


def test_proxy_sweep_runs_once_per_client(monkeypatch) -> None:
    monkeypatch.setattr(proxy_http, "PROXY_FALLBACK_ENABLED", True)
    calls: list[str] = []

    def fake_get(url, proxies=None, timeout=None, **kwargs):
        calls.append("proxy" if proxies else ("list" if url == proxy_http.PROXYSCRAPE_ENDPOINT else "direct"))
        if url == proxy_http.PROXYSCRAPE_ENDPOINT:
            return MagicMock(status_code=200, text="1.1.1.1:80\n2.2.2.2:80\n", raise_for_status=lambda: None)
        if proxies:
            raise requests.ConnectionError("dead proxy")
        return MagicMock(status_code=429)

    monkeypatch.setattr(proxy_http.requests, "get", fake_get)
    client = proxy_http.ProxyAwareHttpClient()

    assert client.get("https://feed.example/calendar.xml").status_code == 429
    assert client.get("https://feed.example/calendar.xml").status_code == 429
    assert calls == ["direct", "list", "proxy", "proxy", "direct"]
