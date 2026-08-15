from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, List, Optional

import requests

from config.settings import NEWS_CALENDAR_URL


class NewsCalendarClient:
    """Fetch this week's high-impact USD events from the free ForexFactory
    JSON feed so the news blackout runs hands-free."""

    MAX_EVENTS = 50

    def fetch_high_impact_events(self) -> List[dict[str, Any]]:
        response = requests.get(
            NEWS_CALENDAR_URL,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (gold-bot calendar sync)"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Calendar feed returned {response.status_code}")

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Calendar feed returned unexpected payload")

        events: List[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            country = str(item.get("country", "")).upper()
            impact = str(item.get("impact", "")).upper()
            if country != "USD" or impact != "HIGH":
                continue
            timestamp = self._parse_date(item.get("date"))
            if timestamp is None:
                continue
            events.append(
                {
                    "timestamp": timestamp,
                    "label": str(item.get("title", "high-impact event"))[:80],
                }
            )

        events.sort(key=lambda event: event["timestamp"])
        return events[: self.MAX_EVENTS]

    @staticmethod
    def _parse_date(raw: Any) -> Optional[int]:
        if not isinstance(raw, str) or not raw:
            return None
        try:
            return int(datetime.fromisoformat(raw).timestamp())
        except (ValueError, OverflowError):
            return None

    @staticmethod
    def merge_with_manual(existing_raw: Optional[str], fetched: List[dict[str, Any]], now_ts: int) -> List[dict[str, Any]]:
        """Auto events are replaced wholesale; dashboard-added (manual) events
        survive refreshes until they are in the past."""
        manual: List[dict[str, Any]] = []
        if isinstance(existing_raw, str) and existing_raw:
            try:
                existing = json.loads(existing_raw)
            except (TypeError, ValueError):
                existing = []
            if isinstance(existing, list):
                for event in existing:
                    if not isinstance(event, dict) or not event.get("manual"):
                        continue
                    try:
                        if int(event.get("timestamp", 0)) > int(now_ts) - 3600:
                            manual.append(event)
                    except (TypeError, ValueError):
                        continue

        merged = manual + [
            {**event, "manual": False}
            for event in fetched
            if int(event.get("timestamp", 0)) > int(now_ts) - 3600
        ]
        merged.sort(key=lambda event: int(event.get("timestamp", 0)))
        return merged


def refresh_news_blackouts(repository: Any, now_ts: int) -> int:
    """Fetch and store upcoming blackout windows; returns the event count."""
    client = NewsCalendarClient()
    fetched = client.fetch_high_impact_events()
    try:
        existing_raw = repository.get_kv("upcoming_news_events_json")
    except Exception:
        existing_raw = None
    merged = client.merge_with_manual(
        existing_raw if isinstance(existing_raw, str) else None, fetched, now_ts
    )
    repository.set_kv("upcoming_news_events_json", json.dumps(merged))
    logging.info("News calendar refreshed: %d upcoming high-impact USD events", len(merged))
    return len(merged)
