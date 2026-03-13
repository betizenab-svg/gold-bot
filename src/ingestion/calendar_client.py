from __future__ import annotations

import json
import logging
import re
from io import StringIO
from typing import Any, Dict, List

import pandas as pd
import requests

from src.persistence.repository import Repository


class EconomicCalendarClient:
    """Economic Calendar Data Client.

    Attempts to parse free public HTML calendar tables, then falls back to
    manual DB overrides if no live parseable data is available.
    """

    FOREX_FACTORY_URL = "https://www.forexfactory.com/calendar"

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

    def _fetch_live_events(self) -> List[Dict[str, Any]]:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; XAUUSD-Signal-Bot/1.0)",
            "Accept-Language": "en-US,en;q=0.9",
        }

        response = requests.get(self.FOREX_FACTORY_URL, headers=headers, timeout=15)
        response.raise_for_status()

        tables = pd.read_html(StringIO(response.text))
        if not tables:
            return []

        events: List[Dict[str, Any]] = []
        for table in tables:
            columns = {str(col).strip().lower(): col for col in table.columns}
            if "actual" not in columns or "forecast" not in columns:
                continue

            currency_col = columns.get("currency")
            event_col = columns.get("event")
            impact_col = columns.get("impact")
            previous_col = columns.get("previous")
            actual_col = columns["actual"]
            forecast_col = columns["forecast"]

            for _, row in table.iterrows():
                if currency_col is not None and str(row.get(currency_col, "")).strip().upper() != "USD":
                    continue

                if impact_col is not None:
                    impact_text = str(row.get(impact_col, "")).lower()
                    if "high" not in impact_text:
                        continue

                actual_val = self._parse_float(row.get(actual_col))
                forecast_val = self._parse_float(row.get(forecast_col))
                if actual_val is None or forecast_val is None:
                    continue

                previous_val = self._parse_float(row.get(previous_col)) if previous_col is not None else None
                if previous_val is not None:
                    sigma = max(abs(previous_val) * 0.1, 0.01)
                else:
                    sigma = max(abs(forecast_val) * 0.1, 0.01)

                event_name = str(row.get(event_col, "USD Event")).strip() if event_col is not None else "USD Event"
                if not event_name:
                    event_name = "USD Event"

                events.append(
                    {
                        "event_name": event_name,
                        "forecast": forecast_val,
                        "actual": actual_val,
                        "historical_sigma": sigma,
                        "usd_impact_direction": 1,
                    }
                )

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
