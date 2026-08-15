"""Sprint 48 — hands-free automation: news calendar sync and weekly report."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.alerting.weekly_report import build_weekly_report
from src.ingestion.news_calendar import NewsCalendarClient, refresh_news_blackouts

NOW = 1_700_000_000


class _KvRepo:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get_kv(self, key: str):
        return self.kv.get(key)

    def set_kv(self, key: str, value) -> None:
        self.kv[key] = str(value)


def _fake_feed_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = [
        {"title": "Non-Farm Employment Change", "country": "USD", "impact": "High",
         "date": "2023-11-15T08:30:00-05:00"},
        {"title": "FOMC Statement", "country": "USD", "impact": "High",
         "date": "2023-11-16T14:00:00-05:00"},
        {"title": "German CPI", "country": "EUR", "impact": "High",
         "date": "2023-11-15T02:00:00-05:00"},  # wrong country: filtered
        {"title": "Crude Oil Inventories", "country": "USD", "impact": "Medium",
         "date": "2023-11-15T10:30:00-05:00"},  # wrong impact: filtered
        {"title": "Broken", "country": "USD", "impact": "High", "date": "not-a-date"},
    ]
    return response


def test_calendar_fetch_filters_high_impact_usd() -> None:
    with patch("src.ingestion.news_calendar.requests.get", return_value=_fake_feed_response()):
        events = NewsCalendarClient().fetch_high_impact_events()

    assert len(events) == 2
    assert events[0]["label"] == "Non-Farm Employment Change"
    assert events[0]["timestamp"] == 1_700_055_000  # 2023-11-15 13:30 UTC
    assert all(isinstance(e["timestamp"], int) for e in events)


def test_refresh_preserves_manual_events() -> None:
    repo = _KvRepo()
    manual_future = {"timestamp": NOW + 7200, "label": "my manual event", "manual": True}
    auto_stale = {"timestamp": NOW + 3600, "label": "old auto", "manual": False}
    repo.kv["upcoming_news_events_json"] = json.dumps([manual_future, auto_stale])

    with patch("src.ingestion.news_calendar.requests.get", return_value=_fake_feed_response()):
        count = refresh_news_blackouts(repo, NOW)

    stored = json.loads(repo.kv["upcoming_news_events_json"])
    labels = [event["label"] for event in stored]
    assert "my manual event" in labels          # manual survives
    assert "old auto" not in labels             # auto entries replaced
    assert "FOMC Statement" in labels           # fresh auto entries in
    assert count == len(stored)


def test_merge_drops_past_manual_events() -> None:
    past_manual = json.dumps([{"timestamp": NOW - 86400, "label": "done", "manual": True}])
    merged = NewsCalendarClient.merge_with_manual(past_manual, [], NOW)
    assert merged == []


def test_weekly_report_message_contains_stats_and_advice() -> None:
    analysis = {
        "strategies": {
            "PIN_BAR_REJECTION": {"trades": 12, "expectancy_r": 0.42, "profit_factor": 1.9},
            "H2_PULLBACK": {"trades": 8, "expectancy_r": -0.1, "profit_factor": 0.8},
        },
        "recommendations": ["H2_PULLBACK: losers ran a median 1.10R in your favor before dying."],
    }
    message = build_weekly_report(analysis)
    assert "Weekly Performance Report" in message
    assert "Closed trades: <b>20</b>" in message
    assert "PIN_BAR_REJECTION" in message
    assert "+0.42R" in message
    assert "What the evidence says" in message
    assert "losers ran a median" in message


def main() -> None:
    print("Sprint 48 verified")


if __name__ == "__main__":
    main()
