from __future__ import annotations

import logging
from typing import Any, Dict

from config.settings import SURPRISE_FACTOR_THRESHOLD


class SurpriseFactorEngine:
    """Consensus Variance / Surprise Factor Engine.

    Measures the divergence between Actual and Forecasted economic data
    and evaluates "Double Whammy" contrarian conditions.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)

    def calculate_surprise_factor(
        self, actual: float, forecast: float, sigma: float
    ) -> float:
        """Calculate the Surprise Factor.

        Formula: (actual - forecast) / sigma

        Args:
            actual: The actual economic data release value.
            forecast: The consensus forecast value.
            sigma: The historical standard deviation of the data series.

        Returns:
            The surprise factor as a float. Returns 0.0 if sigma is 0.
        """
        if sigma == 0.0:
            self.logger.warning("Sigma is zero; defaulting surprise factor to 0.0")
            return 0.0

        return (actual - forecast) / sigma

    def evaluate_double_whammy(self, event_dict: Dict[str, Any]) -> str:
        """Evaluate the Double Whammy contrarian logic for a single event.

        The Double Whammy triggers when the herd is caught completely offside:
        the actual data inverts the forecast direction AND the magnitude
        exceeds the threshold.

        Args:
            event_dict: A dictionary with keys: actual, forecast,
                        historical_sigma, usd_impact_direction.

        Returns:
            'CONTRARIAN_BULLISH', 'CONTRARIAN_BEARISH', or 'NEUTRAL'.
        """
        actual = float(event_dict["actual"])
        forecast = float(event_dict["forecast"])
        sigma = float(event_dict["historical_sigma"])

        surprise = self.calculate_surprise_factor(actual, forecast, sigma)

        # Double Whammy: forecast was positive (Bullish USD / Bearish Gold)
        # but actual came in negative (Bearish USD / Bullish Gold)
        if (
            forecast > 0
            and actual < 0
            and abs(surprise) >= SURPRISE_FACTOR_THRESHOLD
        ):
            return "CONTRARIAN_BULLISH"

        # Double Whammy: forecast was negative (Bearish USD / Bullish Gold)
        # but actual came in positive (Bullish USD / Bearish Gold)
        if (
            forecast < 0
            and actual > 0
            and abs(surprise) >= SURPRISE_FACTOR_THRESHOLD
        ):
            return "CONTRARIAN_BEARISH"

        return "NEUTRAL"
