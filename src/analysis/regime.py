from __future__ import annotations

import logging
import math

import pandas as pd


class RegimeDetector:
    """Detects the macro regime by correlating Gold prices with TIPS yields.

    Regime classification based on the 60-day Pearson correlation between
    XAUUSD daily closes and U.S. 10-Year TIPS (DFII10) yields:

    - REGIME_NORMAL:     correlation < -0.5  (Yields drive price)
    - REGIME_TRANSITION: -0.5 <= correlation <= -0.2
    - REGIME_DECOUPLED:  correlation > -0.2  (Sovereign risk drives price)
    - REGIME_UNKNOWN:    Insufficient data or NaN
    """

    CORRELATION_WINDOW = 60  # trailing days

    THRESHOLD_NORMAL = -0.5
    THRESHOLD_DECOUPLED = -0.2

    def calculate_correlation(
        self,
        gold_daily_series: pd.Series,
        tips_daily_series: pd.Series,
    ) -> float:
        """Calculate the trailing 60-day Pearson correlation.

        Args:
            gold_daily_series: pd.Series with date index and daily close prices.
            tips_daily_series: pd.Series with date index and TIPS yield values.

        Returns:
            Pearson correlation coefficient as a float, or NaN if insufficient data.
        """
        merged = pd.concat(
            [gold_daily_series.rename("gold"), tips_daily_series.rename("tips")],
            axis=1,
            join="inner",
        ).dropna()

        if len(merged) < 2:
            logging.warning(
                "Insufficient aligned data points for correlation: %d", len(merged)
            )
            return float("nan")

        # Take the trailing CORRELATION_WINDOW rows
        window = merged.tail(self.CORRELATION_WINDOW)

        correlation = window["gold"].corr(window["tips"])

        logging.info(
            "Regime correlation: %.4f (computed over %d aligned days)",
            correlation,
            len(window),
        )

        return float(correlation)

    def determine_regime(self, correlation_value: float) -> str:
        """Classify the market regime based on the correlation value.

        Args:
            correlation_value: Pearson correlation coefficient.

        Returns:
            One of 'REGIME_NORMAL', 'REGIME_DECOUPLED',
            'REGIME_TRANSITION', or 'REGIME_UNKNOWN'.
        """
        if math.isnan(correlation_value):
            return "REGIME_UNKNOWN"

        if correlation_value < self.THRESHOLD_NORMAL:
            return "REGIME_NORMAL"

        if correlation_value > self.THRESHOLD_DECOUPLED:
            return "REGIME_DECOUPLED"

        return "REGIME_TRANSITION"
