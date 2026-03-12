from __future__ import annotations

import logging
from typing import Optional

from config.settings import (
    LONG_BIAS_MULTIPLIER_ACTIVE,
    LONG_BIAS_MULTIPLIER_INACTIVE,
    SOVEREIGN_ACCUMULATION_THRESHOLD,
)
from src.persistence.repository import Repository

DEFAULT_NET_PURCHASES = 400.0


class SovereignProxy:
    """Evaluates central bank accumulation to produce a Long Bias Multiplier.

    When quarterly net purchases exceed the threshold, bullish technical
    signals should be scaled up by the active multiplier.
    """

    def get_net_purchases(self, repository: Repository) -> float:
        """Retrieve macro_cb_net_purchases from kv_store.

        If the key does not exist, initialises it with the default value
        of 400.0 tonnes and returns that default.
        """
        raw = repository.get_kv("macro_cb_net_purchases")
        if raw is None:
            logging.info(
                "macro_cb_net_purchases not found; defaulting to %.1f",
                DEFAULT_NET_PURCHASES,
            )
            repository.set_kv("macro_cb_net_purchases", str(DEFAULT_NET_PURCHASES))
            return DEFAULT_NET_PURCHASES

        try:
            return float(raw)
        except (ValueError, TypeError):
            logging.warning(
                "Invalid macro_cb_net_purchases value '%s'; defaulting to %.1f",
                raw,
                DEFAULT_NET_PURCHASES,
            )
            repository.set_kv("macro_cb_net_purchases", str(DEFAULT_NET_PURCHASES))
            return DEFAULT_NET_PURCHASES

    def calculate_multiplier(self, net_purchases: float) -> float:
        """Return the Long Bias Multiplier based on the accumulation threshold.

        Args:
            net_purchases: Central bank net purchases in tonnes per quarter.

        Returns:
            LONG_BIAS_MULTIPLIER_ACTIVE  (1.25) if net_purchases > threshold,
            LONG_BIAS_MULTIPLIER_INACTIVE (1.0)  otherwise.
        """
        if net_purchases > SOVEREIGN_ACCUMULATION_THRESHOLD:
            return float(LONG_BIAS_MULTIPLIER_ACTIVE)
        return float(LONG_BIAS_MULTIPLIER_INACTIVE)
