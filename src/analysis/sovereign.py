from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from config import settings
from config.settings import (
    LONG_BIAS_MULTIPLIER_ACTIVE,
    LONG_BIAS_MULTIPLIER_INACTIVE,
    SOVEREIGN_ACCUMULATION_THRESHOLD,
)
from src.persistence.repository import Repository

KV_VALUE = "macro_cb_net_purchases"
KV_SOURCE = "macro_cb_net_purchases_source"
# A quarterly figure stays usable until two newer quarters could have replaced it.
MAX_QUARTERS_OLD = 2


def _quarter_index(label: str) -> Optional[int]:
    match = re.fullmatch(r"\s*(\d{4})\s*-?\s*Q([1-4])\s*", str(label or ""), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)) * 4 + int(match.group(2)) - 1


def _current_quarter_index(now: Optional[datetime] = None) -> int:
    moment = now or datetime.now(timezone.utc)
    return moment.year * 4 + (moment.month - 1) // 3


class SovereignProxy:
    """Central-bank gold buying (tonnes per quarter, World Gold Council).

    There is no free automatic feed, so the figure is entered by hand
    (CB_NET_PURCHASES_TONNES + CB_NET_PURCHASES_QUARTER). Without a real,
    recent figure the check stays off; a made-up default is never used.
    """

    def manual_figure(self, now: Optional[datetime] = None) -> Optional[tuple[float, str]]:
        raw_value = settings.CB_NET_PURCHASES_TONNES
        quarter = settings.CB_NET_PURCHASES_QUARTER
        quarter_index = _quarter_index(quarter)
        if not raw_value or quarter_index is None:
            return None
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            logging.warning("CB_NET_PURCHASES_TONNES is not a number: %r", raw_value)
            return None
        if _current_quarter_index(now) - quarter_index > MAX_QUARTERS_OLD:
            logging.info("Central-bank figure for %s is too old; check stays off", quarter)
            return None
        return value, quarter.strip().upper()

    def get_net_purchases(self, repository: Repository) -> Optional[float]:
        figure = self.manual_figure()
        if figure is not None:
            value, quarter = figure
            repository.set_kv(KV_VALUE, str(value))
            repository.set_kv(KV_SOURCE, f"manual:{quarter}")
            return value
        return None

    def calculate_multiplier(self, net_purchases: Optional[float]) -> float:
        """1.25 when real quarterly buying is above the threshold, else 1.0."""
        if net_purchases is not None and net_purchases > SOVEREIGN_ACCUMULATION_THRESHOLD:
            return float(LONG_BIAS_MULTIPLIER_ACTIVE)
        return float(LONG_BIAS_MULTIPLIER_INACTIVE)
