from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

import requests

from src.ingestion.proxy_http import ProxyAwareHttpClient
from src.persistence.repository import Repository


class EconomicCalendarClient:
    """Economic Calendar Data Client.

    Parses the ForexFactory weekly XML feed, then falls back to manual
    DB overrides if no usable live events are available.
    """

    FOREX_FACTORY_XML_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    CACHE_KEY = "calendar_live_events_cache_json"

    def __init__(self, repository: Repository | None = None) -> None:
        self.logger = logging.getLogger(__name__)
        self.repository = repository

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None

        cleaned = re.sub(r"[^0-9.\-]", "", text)
        if cleaned in {"", "-", ".", "-."}:
            return None

        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _impact_is_high(impact: str) -> bool:
        normalized = impact.strip().lower()
        return normalized in {"high", "holiday"} or "high" in normalized

    def _manual_override_events(self) -> List[Dict[str, Any]]:
        if self.repository is None:
            return []

        raw = self.repository.get_kv("manual_calendar_events_json")
        if not raw:
            return []

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.warning("manual_calendar_events_json is not valid JSON")
            return []

        if not isinstance(decoded, list):
            self.logger.warning("manual_calendar_events_json must be a JSON list")
            return []

        normalized: List[Dict[str, Any]] = []
        for item in decoded:
            if not isinstance(item, dict):
                continue
            required = {"event_name", "forecast", "actual", "historical_sigma", "usd_impact_direction"}
            if not required.issubset(item.keys()):
                continue
            normalized.append(item)

        return normalized

    def _read_cached_events(self) -> List[Dict[str, Any]]:
        if self.repository is None:
            return []

        raw = self.repository.get_kv(self.CACHE_KEY)
        if not raw:
            return []

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return []

        if not isinstance(decoded, list):
            return []

        events: List[Dict[str, Any]] = []
        for item in decoded:
            if not isinstance(item, dict):
                continue
            required = {"event_name", "forecast", "actual", "historical_sigma", "usd_impact_direction"}
            if required.issubset(item.keys()):
                events.append(item)

        return events

    def _fetch_live_events(self) -> List[Dict[str, Any]]:
        http_client = ProxyAwareHttpClient(self.logger)
        response: requests.Response | None = None
        for attempt in range(3):
            response = http_client.get(self.FOREX_FACTORY_XML_URL, timeout=15)

            # Backoff on rate limiting.
            if response.status_code == 429 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break

        if response is None:
            raise RuntimeError("Economic calendar feed request returned no response")

        response.raise_for_status()

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise RuntimeError("Invalid XML response from economic calendar feed") from exc

        nodes = root.findall("event")
        if not nodes:
            return []

        events: List[Dict[str, Any]] = []
        for event in nodes:
            currency = (event.findtext("country") or "").strip().upper()
            if currency != "USD":
                continue

            impact = (event.findtext("impact") or "").strip()
            if not self._impact_is_high(impact):
                continue

            forecast_val = self._parse_float(event.findtext("forecast"))
            actual_val = self._parse_float(event.findtext("actual"))
            previous_val = self._parse_float(event.findtext("previous"))

            # Use only released events for surprise calculation.
            if forecast_val is None or actual_val is None:
                continue

            sigma = max(abs(previous_val) * 0.1, 0.01) if previous_val is not None else max(abs(forecast_val) * 0.1, 0.01)

            event_name = (event.findtext("title") or "USD Event").strip() or "USD Event"

            events.append(
                {
                    "event_name": event_name,
                    "forecast": forecast_val,
                    "actual": actual_val,
                    "historical_sigma": sigma,
                    "usd_impact_direction": 1,
                }
            )

        if events and self.repository is not None:
            self.repository.set_kv(self.CACHE_KEY, events)

        return events

    def fetch_latest_events(self) -> List[Dict[str, Any]]:
        """Fetch the latest high-impact economic events.

        Returns a list of dictionaries, each containing:
            - event_name (str)
            - forecast (float)
            - actual (float)
            - historical_sigma (float)
            - usd_impact_direction (int: 1 for direct, -1 for inverse)
        """
        try:
            events = self._fetch_live_events()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429:
                cached_events = self._read_cached_events()
                if cached_events:
                    self.logger.info(
                        "Economic calendar feed rate-limited; using %d cached events",
                        len(cached_events),
                    )
                    return cached_events
                self.logger.info("Economic calendar feed rate-limited and no cached events available")
            else:
                self.logger.warning("Economic calendar live fetch failed: %s", exc)
            events = []
        except Exception as exc:
            self.logger.warning("Economic calendar live fetch failed: %s", exc)
            events = []

        if events:
            self.logger.info("Fetched %d live economic calendar events", len(events))
            return events

        manual_events = self._manual_override_events()
        if manual_events:
            self.logger.info("Using %d manual economic calendar override events", len(manual_events))
            return manual_events

        self.logger.info("No live or manual economic calendar events available")
        return []
