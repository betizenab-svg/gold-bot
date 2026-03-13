from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from config.settings import ATR_SL_MULTIPLIER
from src.analysis.position_sizing import LotSizeCalculator
from src.domain.signal import Signal


class SignalFactory:
    """Construct executable signal objects from actionable setups."""

    LOT_SIZE_TABLE_MARKER = "\n\n[LOT_SIZE_TABLE]\n"

    def calculate_parameters(
        self,
        trade_direction: str,
        zone_dict: dict[str, Any],
        atr: float,
    ) -> tuple[float, float, float, float]:
        direction = trade_direction.upper()
        if "entry_price" in zone_dict and "sl_price" in zone_dict:
            entry = float(zone_dict["entry_price"])
            sl = float(zone_dict["sl_price"])
            if direction == "LONG":
                risk = entry - sl
                tp1 = entry + (1.5 * risk)
                tp2 = entry + (3.0 * risk)
            elif direction == "SHORT":
                risk = sl - entry
                tp1 = entry - (1.5 * risk)
                tp2 = entry - (3.0 * risk)
            else:
                raise ValueError(f"Unsupported trade direction: {trade_direction}")
        else:
            price_top = float(zone_dict["price_top"])
            price_bottom = float(zone_dict["price_bottom"])
            atr_value = float(atr)

            if direction == "LONG":
                entry = price_top
                sl = price_bottom - (ATR_SL_MULTIPLIER * atr_value)
                risk = entry - sl
                tp1 = entry + (1.5 * risk)
                tp2 = entry + (3.0 * risk)
            elif direction == "SHORT":
                entry = price_bottom
                sl = price_top + (ATR_SL_MULTIPLIER * atr_value)
                risk = sl - entry
                tp1 = entry - (1.5 * risk)
                tp2 = entry - (3.0 * risk)
            else:
                raise ValueError(f"Unsupported trade direction: {trade_direction}")

        if risk <= 0:
            raise ValueError("Signal risk must be positive")

        return (
            round(entry, 2),
            round(sl, 2),
            round(tp1, 2),
            round(tp2, 2),
        )

    def build_signal(
        self,
        symbol: str,
        trade_direction: str,
        zone_dict: dict[str, Any],
        atr: float,
        score: int,
        timestamp: int,
    ) -> Signal:
        signal_type = trade_direction.upper()
        entry, sl, tp1, tp2 = self.calculate_parameters(signal_type, zone_dict, atr)
        zone_status = str(zone_dict.get("status", "UNKNOWN")).title()
        zone_type = str(zone_dict.get("type", "ZONE")).replace("_", " ")
        base_reasoning = f"Score: {int(score)}. Entry off {zone_status} {zone_type}."
        lot_size_table = LotSizeCalculator().generate_table(entry, sl)
        reasoning = f"{base_reasoning}{self.LOT_SIZE_TABLE_MARKER}{lot_size_table}"

        zone_id = zone_dict.get("id", zone_dict.get("zone_id"))
        strategy_key = zone_dict.get("strategy")
        dedupe_target = zone_id
        if dedupe_target is None:
            dedupe_target = (
                f"{strategy_key}|{round(entry, 2):.2f}|{round(sl, 2):.2f}"
                if strategy_key is not None
                else f"{round(entry, 2):.2f}|{round(sl, 2):.2f}"
            )
        date_string = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hash_input = f"{symbol}|{signal_type}|{dedupe_target}|{date_string}"
        signal_hash = hashlib.md5(hash_input.encode("utf-8")).hexdigest()

        return Signal(
            symbol=symbol,
            signal_type=signal_type,
            entry_price=entry,
            sl_price=sl,
            tp1_price=tp1,
            tp2_price=tp2,
            score=int(score),
            reasoning=reasoning,
            timestamp=int(timestamp),
            signal_hash=signal_hash,
        )
