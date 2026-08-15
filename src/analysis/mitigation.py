from __future__ import annotations

from typing import Any, Dict, List

from src.domain.candle import Candle


class ZoneLifecycleManager:
    """Evaluate zone mitigation and invalidation from the current candle."""

    @staticmethod
    def _is_bullish_zone(zone_type: str) -> bool:
        return "BULLISH" in zone_type.upper()

    def evaluate_zones(
        self,
        current_candle: Candle,
        active_zones: List[dict[str, Any]],
    ) -> List[dict[str, Any]]:
        updated_zones: List[dict[str, Any]] = []

        for zone in active_zones:
            zone_type = str(zone.get("type", ""))
            current_status = str(zone.get("status", "")).upper()
            price_top = float(zone.get("price_top", 0.0))
            price_bottom = float(zone.get("price_bottom", 0.0))
            new_status: str | None = None

            if self._is_bullish_zone(zone_type):
                if float(current_candle.close) < price_bottom:
                    new_status = "INVALIDATED"
                elif float(current_candle.low) <= price_top:
                    # First touch mitigates; a second touch consumes the zone
                    # (book consensus: fresh zones only, >=2 touches = dead).
                    new_status = "INVALIDATED" if current_status == "MITIGATED" else "MITIGATED"
            else:
                if float(current_candle.close) > price_top:
                    new_status = "INVALIDATED"
                elif float(current_candle.high) >= price_bottom:
                    new_status = "INVALIDATED" if current_status == "MITIGATED" else "MITIGATED"

            if new_status is None or new_status == zone.get("status"):
                continue

            updated_zone = dict(zone)
            updated_zone["status"] = new_status
            updated_zones.append(updated_zone)

        return updated_zones
