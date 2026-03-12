from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

DFII10_SERIES = "DFII10"


class MacroDataError(RuntimeError):
    """Raised when macro data fetching fails."""
    pass


class FredMacroClient:
    """Fetches macroeconomic data from FRED (Federal Reserve Economic Data)."""

    def fetch_10y_tips_yield(self, days: int = 90) -> pd.Series:
        """Fetch the U.S. 10-Year TIPS yield (DFII10) from FRED.

        Args:
            days: Number of calendar days of history to request.

        Returns:
            A pd.Series with timezone-naive UTC dates as the index
            and TIPS yield values as floats.

        Raises:
            MacroDataError: If the data cannot be fetched or is empty.
        """
        try:
            from pandas_datareader import data as pdr
        except ImportError as exc:
            raise MacroDataError(
                "pandas-datareader is not installed. "
                "Run: pip install pandas-datareader"
            ) from exc

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)

        try:
            frame = pdr.DataReader(
                DFII10_SERIES,
                "fred",
                start=start_date,
                end=end_date,
            )
        except Exception as exc:
            logging.error("FRED data fetch failed: %s", exc)
            raise MacroDataError(f"Failed to fetch {DFII10_SERIES} from FRED") from exc

        if frame is None or frame.empty:
            raise MacroDataError(f"FRED returned empty data for {DFII10_SERIES}")

        series = frame[DFII10_SERIES].dropna()

        if series.empty:
            raise MacroDataError(f"No valid data points in {DFII10_SERIES} series")

        # Ensure timezone-naive index (FRED typically returns naive dates)
        if hasattr(series.index, "tz") and series.index.tz is not None:
            series.index = series.index.tz_localize(None)

        series.index = pd.to_datetime(series.index).normalize()

        logging.info(
            "Fetched %d TIPS yield data points from FRED (%s to %s)",
            len(series),
            series.index.min().date(),
            series.index.max().date(),
        )

        return series
