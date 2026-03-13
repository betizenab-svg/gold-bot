from __future__ import annotations

import json
import logging
from typing import List, Optional

import requests

from config.settings import COT_LOOKBACK_WEEKS
from src.persistence.repository import Repository

class CotClient:
    """Commitment of Traders (COT) Data Client."""

    CFTC_SOCRATA_URL = "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
    GOLD_MARKET_CODE = "088691"

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.logger = logging.getLogger(__name__)
        self.repository = repository

    def _read_manual_override(self) -> List[float]:
        if self.repository is None:
            return []

        raw = self.repository.get_kv("manual_cot_net_positions_json")
        if not raw:
            return []

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.warning("manual_cot_net_positions_json is not valid JSON")
            return []

        if not isinstance(decoded, list):
            self.logger.warning("manual_cot_net_positions_json must be a JSON list")
            return []

        values: List[float] = []
        for item in decoded:
            try:
                values.append(float(item))
            except (TypeError, ValueError):
                continue

        return values[-COT_LOOKBACK_WEEKS:]

    def fetch_historical_net_positions(self) -> List[float]:
        """Fetch historical non-commercial net positions for COMEX gold.

        Source: CFTC Socrata API (`kh3c-gbw2`, keyless/public endpoint).
        Returns oldest-to-newest values for the configured lookback window.
        Falls back to manual DB override key `manual_cot_net_positions_json`.
        """
        params = {
            "cftc_contract_market_code": self.GOLD_MARKET_CODE,
            "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": str(COT_LOOKBACK_WEEKS * 3),
        }

        try:
            response = requests.get(self.CFTC_SOCRATA_URL, params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            self.logger.warning("COT fetch failed, trying manual override: %s", exc)
            return self._read_manual_override()

        if not isinstance(payload, list):
            self.logger.warning("Unexpected COT payload type, trying manual override")
            return self._read_manual_override()

        positions_desc: List[float] = []
        for row in payload:
            if not isinstance(row, dict):
                continue

            long_raw = row.get("noncomm_positions_long_all")
            short_raw = row.get("noncomm_positions_short_all")
            try:
                long_val = float(long_raw)
                short_val = float(short_raw)
            except (TypeError, ValueError):
                continue

            positions_desc.append(long_val - short_val)
            if len(positions_desc) >= COT_LOOKBACK_WEEKS:
                break

        if not positions_desc:
            self.logger.warning("COT endpoint returned no usable rows, trying manual override")
            return self._read_manual_override()

        positions_asc = list(reversed(positions_desc))
        self.logger.info("Fetched %d COT net position points from CFTC", len(positions_asc))
        return positions_asc
