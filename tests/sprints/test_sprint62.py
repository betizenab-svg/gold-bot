"""Sprint 62 (roadmap milestone 7): public results site, monthly report and
share images, swing-chart timing."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.monthly_report import caption, month_stats, month_trades, previous_month, write_reports
from src.analysis.trade_windows import max_hold_seconds, pending_window_seconds

ROOT = Path(__file__).resolve().parents[2]


def _ledger(path: Path) -> None:
    lines = [
        {"event": "OPEN", "id": 1, "code": "#G1", "symbol": "XAUUSD", "direction": "LONG", "event_time": "2026-09-01T08:00:00Z"},
        {"event": "CLOSE", "id": 1, "code": "#G1", "symbol": "XAUUSD", "direction": "LONG", "result_r": 2.25, "event_time": "2026-09-01T12:00:00Z"},
        {"event": "CLOSE", "id": 2, "code": "#E2", "symbol": "EURUSD", "direction": "SHORT", "result_r": -1.0, "event_time": "2026-09-03T09:00:00Z"},
        {"event": "CANCEL", "id": 3, "code": "#E3", "symbol": "EURUSD", "direction": "SHORT", "event_time": "2026-09-04T09:00:00Z"},
        {"event": "CLOSE", "id": 4, "code": "#G4", "symbol": "XAUUSD", "direction": "LONG", "result_r": -1.0, "event_time": "2026-10-01T09:00:00Z"},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_monthly_report_files_and_caption(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    _ledger(ledger)
    trades = month_trades(ledger, "2026-09")
    assert [t["code"] for t in trades] == ["#G1", "#E2"]
    stats = month_stats(trades)
    assert (stats["net_r"], stats["wins"], stats["losses"]) == (1.25, 1, 1)
    text = caption("2026-09", stats)
    assert "September 2026" in text and "+1.25R" in text and "1 lost" in text
    out = tmp_path / "reports"
    write_reports("2026-09", ledger_path=ledger, out_dir=out)
    write_reports("2026-09", ledger_path=ledger, out_dir=out)
    names = sorted(p.name for p in out.iterdir())
    assert names == ["2026-09-square.png", "2026-09-story.png", "2026-09.pdf", "2026-09.png", "index.json"]
    assert json.loads((out / "index.json").read_text()) == ["2026-09"]
    assert (out / "2026-09.pdf").read_bytes()[:4] == b"%PDF"
    assert "No finished trades" in caption("2026-08", month_stats(month_trades(ledger, "2026-08")))


def test_previous_month() -> None:
    from datetime import datetime, timezone

    assert previous_month(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2025-12"
    assert previous_month(datetime(2026, 10, 1, tzinfo=timezone.utc)) == "2026-09"


def test_swing_charts_get_time_to_fill_and_work() -> None:
    assert pending_window_seconds("XAUUSD") == 90 * 60
    assert pending_window_seconds("XAUUSD_H1") == 3 * 3600
    assert pending_window_seconds("XAUUSD_H4") == 12 * 3600
    assert max_hold_seconds("XAUUSD") == 24 * 3600
    assert max_hold_seconds("XAUUSD_H4") == 96 * 3600


def test_site_pages_link_the_shared_files() -> None:
    site = ROOT / "site"
    for page in ("index.html", "results.html", "how-it-works.html", "verify.html", "reports.html"):
        html = (site / page).read_text(encoding="utf-8")
        assert 'src="config.js"' in html and 'src="assets/app.js"' in html and "data-page=" in html
    assert "freeChannelUrl" in (site / "config.js").read_text() and "vipUrl" in (site / "config.js").read_text()
    assert "Risk warning" in (site / "how-it-works.html").read_text()
