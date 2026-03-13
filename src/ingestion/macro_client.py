from __future__ import annotations

import logging
from io import StringIO
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.ingestion.proxy_http import ProxyAwareHttpClient

DFII10_SERIES = "DFII10"


class MacroDataError(RuntimeError):
    """Raised when macro data fetching fails."""
    pass


class FredMacroClient:
    """Fetches macroeconomic data from FRED (Federal Reserve Economic Data)."""

    FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    @staticmethod
    def _fetch_with_datareader(start_date, end_date) -> pd.DataFrame:
        from pandas_datareader import data as pdr

        return pdr.DataReader(
            DFII10_SERIES,
            "fred",
            start=start_date,
            end=end_date,
        )

    @staticmethod
    def _fetch_with_fred_reader(start_date, end_date) -> pd.DataFrame:
        from pandas_datareader.fred import FredReader

        reader = FredReader(
            symbols=DFII10_SERIES,
            start=start_date,
            end=end_date,
        )
        return reader.read()

    @classmethod
    def _fetch_with_fred_csv(cls, start_date, end_date) -> pd.DataFrame:
        params = {
            "id": DFII10_SERIES,
            "cosd": start_date.isoformat(),
            "coed": end_date.isoformat(),
        }
        response = ProxyAwareHttpClient(logging.getLogger(__name__)).get(
            cls.FRED_CSV_URL,
            params=params,
            timeout=20,
        )
        response.raise_for_status()

        frame = pd.read_csv(StringIO(response.text))
        date_col = None
        for candidate in ("DATE", "date", "observation_date"):
            if candidate in frame.columns:
                date_col = candidate
                break

        if date_col is None or DFII10_SERIES not in frame.columns:
            raise MacroDataError("FRED CSV response is missing expected columns")

        frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
        frame[DFII10_SERIES] = pd.to_numeric(frame[DFII10_SERIES], errors="coerce")
        frame = frame.dropna(subset=[date_col, DFII10_SERIES]).set_index(date_col)
        return frame[[DFII10_SERIES]]

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
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)

        frame: pd.DataFrame | None = None

        try:
            frame = self._fetch_with_fred_csv(start_date, end_date)
        except Exception as exc:
            logging.info("FRED CSV path unavailable, trying pandas-datareader fallback: %s", exc)

        if frame is None or frame.empty:
            try:
                frame = self._fetch_with_datareader(start_date, end_date)
            except Exception as exc:
                logging.info("FRED DataReader path unavailable, trying FredReader fallback: %s", exc)

        if frame is None or frame.empty:
            try:
                frame = self._fetch_with_fred_reader(start_date, end_date)
            except Exception as exc:
                logging.error("FRED FredReader fallback failed: %s", exc)
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
