from __future__ import annotations

import logging
from typing import Any, Dict, List

from config.settings import HIGH_IMPACT_EVENTS


class EconomicCalendarClient:
    """Economic Calendar Data Client.

    Fetches high-impact economic event data. Currently returns mock data
    to avoid complex external API integration at this stage.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)

    def fetch_latest_events(self) -> List[Dict[str, Any]]:
        """Fetch the latest high-impact economic events.

        Returns a list of dictionaries, each containing:
            - event_name (str)
            - forecast (float)
            - actual (float)
            - historical_sigma (float)
            - usd_impact_direction (int: 1 for direct, -1 for inverse)
        """
        self.logger.info("Fetching mock high-impact economic events")
        return list(HIGH_IMPACT_EVENTS)
