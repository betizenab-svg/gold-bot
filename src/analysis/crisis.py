from __future__ import annotations

import logging
import math
from typing import cast

import pandas as pd

from config.settings import DXY_CORRELATION_WINDOW


class CrisisDetector:
    """Detects systemic market fear via Gold-DXY correlation.

    Normally Gold and the U.S. Dollar Index are inversely correlated.
    When the correlation flips positive it signals a crisis environment
    where both assets are being bid simultaneously (flight to safety).

    - correlation > 0.0  -> CRISIS MODE ACTIVE
    - correlation <= 0.0 -> NORMAL MODE
    """

    def calculate_dxy_correlation(
        self,
        gold_daily_series: pd.Series,
        dxy_daily_series: pd.Series,
    ) -> float:
        """Calculate the trailing 20-day Pearson correlation.

        Args:
            gold_daily_series: pd.Series with date index and Gold daily closes.
            dxy_daily_series: pd.Series with date index and DXY daily closes.

        Returns:
            Pearson correlation coefficient as a float, or NaN if insufficient data.
        """
        merged = pd.concat(
            [gold_daily_series.rename("gold"), dxy_daily_series.rename("dxy")],
            axis=1,
            join="inner",
        ).dropna()

        if len(merged) < 2:
            logging.info(
                "Insufficient aligned data for DXY correlation: %d points",
                len(merged),
            )
            return float("nan")

        window = merged.tail(DXY_CORRELATION_WINDOW)

        correlation: float = cast(float, cast(pd.Series, window["gold"]).corr(cast(pd.Series, window["dxy"])))

        logging.info(
            "DXY correlation: %.4f (computed over %d aligned days)",
            correlation,
            len(window),
        )

        return float(correlation)

    def evaluate_crisis_mode(self, correlation_value: float) -> bool:
        """Determine whether crisis mode is active.

        Args:
            correlation_value: Pearson correlation coefficient.

        Returns:
            True if correlation > 0.0 (Crisis Mode Active),
            False otherwise (Normal Mode).
        """
        if math.isnan(correlation_value):
            return False

        return bool(correlation_value > 0.0)
