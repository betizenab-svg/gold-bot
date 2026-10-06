"""Sprint 58 (roadmap milestone 3): years of free history, the proof engine's
scorecards, the luck test, trading costs, switched-off pairs, quiet hours and
the automatic history check."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import scripts.history.sources as sources
from scripts.history import check, report, run_all
from scripts.history.cot import schedule
from src.analysis import evidence
from src.analysis.luck_test import longest_losing_streak, luck_test, max_drawdown
from src.domain.candle import Candle
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer

T0 = 1_789_999_800  # Monday 2026-09-21 13:30 UTC, on a 5-minute boundary


def _repo(tmp_path: Path) -> Repository:
    connection = sqlite3.connect(str(tmp_path / "m3.db"))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


# --- Free history files -----------------------------------------------------------

def test_histdata_rows_are_moved_from_new_york_winter_time_to_utc() -> None:
    text = "\n".join(
        [
            "20240102 180100;2063.6;2064.0;2063.1;2063.9;0",
            "20240102 180000;2063.5;2064.0;2063.0;2063.8;0",
            "garbage line",
        ]
    )
    rows = sources.HistDataSource.parse_csv(text)
    assert [r[0] for r in rows] == [1704236400, 1704236460]  # 23:00 and 23:01 UTC
    assert rows[0][1:5] == (2063.5, 2064.0, 2063.0, 2063.8)


def test_binance_millisecond_and_microsecond_files() -> None:
    old = "open_time,open,high,low,close,volume\n1704067200000,42283.5,42554.5,42261.0,42475.2,1271.7\n"
    new = "1735689600000000,93576.0,94509.4,93489.0,94401.1,1037.2\n"
    assert sources.BinanceSource.parse_csv(old)[0][0] == 1704067200
    assert sources.BinanceSource.parse_csv(new)[0][0] == 1735689600


def test_minute_bars_become_fifteen_minute_bars() -> None:
    base = 1704236400
    minutes = [(base + 60 * i, 10.0 + i, 11.0 + i, 9.0 + i, 10.5 + i, 1.0) for i in range(16)]
    bars = sources.resample(minutes, 900)
    assert len(bars) == 2
    first = bars[0]
    assert first == (base, 10.0, 25.0, 9.0, 24.5, 15.0)
    assert bars[1][0] == base + 900


def test_months_are_saved_and_only_missing_finished_months_are_fetched(tmp_path: Path, monkeypatch) -> None:
    from datetime import datetime, timezone

    monkeypatch.setattr(sources, "HISTORY_DIR", tmp_path)
    sources.save_month("BTCUSD", "M15", 2026, 7, [(1, 1.0, 2.0, 0.5, 1.5, 3.0)])
    assert sources.load_month("BTCUSD", "M15", 2026, 7) == [(1, 1.0, 2.0, 0.5, 1.5, 3.0)]

    binance = MagicMock()
    binance.fetch_month.return_value = [(2, 1.0, 1.0, 1.0, 1.0, 1.0)]
    months = sources.iter_months(sources.parse_month("2026-07"), sources.parse_month("2026-10"))
    saved = sources.fetch_symbol(
        "BTCUSD", "BINANCE", "BTCUSDT", "M15", months,
        binance=binance, now=datetime(2026, 10, 6, tzinfo=timezone.utc),
    )
    assert saved == 2  # August and September; July exists, October is not finished
    fetched = [call.args[2:] for call in binance.fetch_month.call_args_list]
    assert fetched == [(2026, 8), (2026, 9)]


def test_batch_jobs_and_cloud_matrix(monkeypatch) -> None:
    batch = run_all.jobs(["XAUUSD", "BTCUSD"], "2023-10", "2026-09")
    variants = {(s, v) for s, v, *_ in batch}
    assert ("XAUUSD", "base") in variants and ("BTCUSD", "be1.25") in variants
    check_job = next(job for job in batch if job[:2] == ("XAUUSD", "check"))
    assert check_job[3:] == ("2026-04", "2026-09")


def test_positioning_history_is_used_only_after_publication() -> None:
    from datetime import datetime, timedelta, timezone

    first_tuesday = datetime(2024, 1, 2, tzinfo=timezone.utc)
    weeks = [(first_tuesday + timedelta(weeks=i), float(i)) for i in range(30)]
    rows = schedule(weeks)
    assert len(rows) == 5  # needs 26 weeks of history first
    # Tuesday's positions only become public on Friday evening (UTC).
    report_day = first_tuesday + timedelta(weeks=25)
    assert rows[0]["report_date"] == report_day.strftime("%Y-%m-%d")
    assert rows[0]["from"] == int((report_day + timedelta(days=3, hours=20, minutes=30)).timestamp())


# --- Luck test, costs, summaries ------------------------------------------------------

def test_dips_and_losing_streaks() -> None:
    results = [1.0, -1.0, -1.0, 2.0, -1.0, -1.0, -1.0, 0.5]
    assert max_drawdown(results) == 3.0
    assert longest_losing_streak(results) == 3
    luck = luck_test(results * 5, runs=300)
    assert luck["trades"] == 40
    assert luck["bad_luck_max_drawdown_r"] >= luck["typical_max_drawdown_r"]
    assert luck_test(results * 5, runs=300) == luck  # same seed, same answer
    assert luck_test([1.0, -1.0]) == {"trades": 2}


def test_trading_costs_are_charged_in_r() -> None:
    # Gold: 0.30 spread + 2 x 0.10 slippage = 0.50 on a 5.00 stop = 0.1R.
    assert evidence.cost_in_r("XAUUSD", 2400.0, 2395.0) == pytest.approx(0.1)
    assert evidence.cost_in_r("XAUUSD", 2400.0, 2400.0) == 0.0
    stats = evidence.summarize([1.0, -1.0, 2.0, -1.0])
    assert stats["trades"] == 4 and stats["net_r"] == 1.0
    assert stats["profit_factor"] == 1.5 and stats["longest_losing_streak"] == 1


def test_evidence_lookups(tmp_path: Path, monkeypatch) -> None:
    data = {
        "disabled_pairs": [{"symbol": "EURUSD", "strategy": "INSIDE_BAR_TRAP"}],
        "quiet_hours": {"XAUUSD": [3, 4]},
        "baselines": {"XAUUSD|PIN_BAR": {"trades": 80, "expectancy_r": 0.2, "std_r": 1.1}},
    }
    assert evidence.disabled_pair("eurusd", "inside_bar_trap", data)
    assert not evidence.disabled_pair("XAUUSD", "INSIDE_BAR_TRAP", data)
    assert evidence.quiet_hour("XAUUSD", 1704164400, data)  # 03:00 UTC
    assert not evidence.quiet_hour("XAUUSD", 1704164400 + 3 * 3600, data)
    assert evidence.baseline("XAUUSD", "PIN_BAR", data)["trades"] == 80

    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("EVIDENCE_ENABLED", "1")
    assert evidence.load_evidence(path)["quiet_hours"] == {"XAUUSD": [3, 4]}
    monkeypatch.setenv("EVIDENCE_ENABLED", "0")
    assert evidence.load_evidence(path) == {}  # history runs measure raw strategies


# --- Scorecards ----------------------------------------------------------------------

def test_filter_reasons_are_grouped_without_their_numbers() -> None:
    assert report.reason_category("REJECTED", "RSI 53: too high; spread") == report.reason_category(
        "REJECTED", "RSI 61: too high"
    )
    assert report.reason_category("BLOCKED", "Quiet hour: history tests show") == "Quiet hour"
    assert report.reason_category("BLOCKED", "Penalty: big-picture short vs buying") == (
        "Big-picture check (score lowered)"
    )
    assert report.reason_category("WATCHLIST", "") == "Score just under the bar (watchlist)"


def test_walk_forward_judges_each_choice_on_unseen_months() -> None:
    months = [f"2024-{m:02d}" for m in range(1, 13)] + ["2025-01", "2025-02", "2025-03"]
    monthly = {
        "BE_ARM_R=0.75": {m: 1.0 for m in months[:12]} | {m: -1.0 for m in months[12:]},
        "BE_ARM_R=1.0": {m: 0.5 for m in months},
    }
    result = report.walk_forward(monthly, months)
    assert len(result["windows"]) == 1
    assert result["windows"][0]["chosen"] == "BE_ARM_R=0.75"
    assert result["unseen_total_r"] == -3.0  # the in-sample winner lost out of sample


def _trade(ts: int, strategy: str, r: float, status: str = "CLOSED_SL") -> dict:
    return {
        "timestamp": ts, "strategy": strategy, "status": status, "realized_r": r,
        "entry": 2400.0, "sl": 2390.0, "tp2": 2420.0, "direction": "LONG", "score": 82,
    }


def test_losing_pairs_and_hours_are_switched_off() -> None:
    trades = []
    for i in range(40):  # a strategy that loses after costs
        trades.append(_trade(1704164400 + i * 86400, "INSIDE_BAR_TRAP", -1.0 if i % 3 else 1.0))
    for i in range(40):  # a strategy that wins, traded at 14:00 UTC
        trades.append(_trade(1704204000 + i * 86400, "PIN_BAR", 2.0 if i % 2 else -1.0, "CLOSED_TP2"))
    payload = {"symbol": "XAUUSD", "variant": "base", "trades": trades, "ideas": []}
    check_payload = {"symbol": "XAUUSD", "variant": "check", "trades": trades[:10], "ideas": [],
                     "window": ["2024-01", "2024-02"]}
    _report, built, markdown = report.build({("base", "XAUUSD"): payload, ("check", "XAUUSD"): check_payload})

    assert [p["strategy"] for p in built["disabled_pairs"]] == ["INSIDE_BAR_TRAP"]
    assert built["quiet_hours"] == {"XAUUSD": [3]}
    assert built["baselines"]["XAUUSD|PIN_BAR"]["trades"] == 40
    assert built["check_window"]["from"] == "2024-01"
    assert built["markets"]["XAUUSD"]["trades"] == 80
    assert "XAUUSD" in markdown


def test_history_check_fails_only_on_a_clear_drop() -> None:
    assert check.allowed_drop(4.0) == 3.0
    assert check.allowed_drop(40.0) == 10.0
    assert check.compare({"XAUUSD": 20.0}, {"XAUUSD": 16.0}) == []
    problems = check.compare({"XAUUSD": 20.0, "EURUSD": 5.0}, {"XAUUSD": 9.0, "EURUSD": 4.0})
    assert len(problems) == 1 and problems[0].startswith("XAUUSD")


# --- The live bot uses the evidence -----------------------------------------------

def test_switched_off_pair_is_skipped(monkeypatch) -> None:
    import src.core.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "disabled_pair", lambda s, st: (s, st) == ("EURUSD", "PIN_BAR"))
    orchestrator = orchestrator_module.PulseOrchestrator(telegram_client_factory=MagicMock)
    assert not orchestrator._strategy_allowed({"strategy": "PIN_BAR"}, "EURUSD")
    assert orchestrator._strategy_allowed({"strategy": "PIN_BAR"}, "XAUUSD")


def test_quiet_hour_blocks_and_logs_the_idea(tmp_path: Path, monkeypatch) -> None:
    import src.core.orchestrator as orchestrator_module
    from src.validation.validator import DataValidator

    repository = _repo(tmp_path)
    candles = [
        Candle("XAUUSD", "M5", T0 + i * 300, 2400.0, 2401.0, 2399.0, 2400.5, 0.0) for i in range(5)
    ]
    client = MagicMock()
    client.fetch_latest_candles.return_value = candles
    client.last_source = {"XAUUSD": "YAHOO"}
    orchestrator = orchestrator_module.PulseOrchestrator(telegram_client_factory=MagicMock)
    setup = {"strategy": "PIN_BAR", "trade_direction": "LONG", "order_type": "MARKET", "entry_price": 2400.5}
    monkeypatch.setattr(orchestrator, "_detect_trade_setup", lambda *a, **k: setup)
    monkeypatch.setattr(orchestrator_module, "quiet_hour", lambda symbol, ts: True)

    generated, errors = orchestrator._pulse_symbol(repository, client, DataValidator(), "XAUUSD", "M5")

    assert generated == 0
    rows = repository.query("SELECT classification, vetoes FROM setup_log;")
    assert rows and rows[-1][0] == "BLOCKED" and rows[-1][1].startswith("Quiet hour")
    assert repository.query("SELECT COUNT(*) FROM signals;")[0][0] == 0
    repository.close()


def test_owner_is_told_when_live_results_fall_below_history(tmp_path: Path, monkeypatch) -> None:
    import src.core.orchestrator as orchestrator_module

    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"baselines": {"XAUUSD|PIN_BAR": {"trades": 90, "expectancy_r": 0.3, "std_r": 1.2}}}))
    monkeypatch.setenv("EVIDENCE_ENABLED", "1")
    monkeypatch.setattr(evidence, "EVIDENCE_PATH", path)
    monkeypatch.setitem(evidence._cache, "mtime", None)
    sent = []
    monkeypatch.setattr(orchestrator_module, "notify_admin", lambda text, **kw: sent.append(text))

    repository = MagicMock()
    repository.get_kv.return_value = None
    repository.get_closed_results_since.return_value = [("PIN_BAR", "XAUUSD", -1.0)] * 12
    orchestrator = orchestrator_module.PulseOrchestrator(telegram_client_factory=MagicMock)
    orchestrator._maybe_check_live_vs_history(repository)
    assert sent and "PIN_BAR on XAUUSD" in sent[0]

    sent.clear()
    repository.get_kv.return_value = str(int(__import__("time").time()))
    orchestrator._maybe_check_live_vs_history(repository)
    assert sent == []  # once a week only
