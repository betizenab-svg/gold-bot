from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import ATR_SL_MULTIPLIER, SL_MIN_ATR_MULT, SL_MIN_USD
from src.analysis.position_sizing import LotSizeCalculator
from src.domain.signal import Signal


class SignalFactory:
    """Construct executable signal objects from actionable setups."""

    LOT_SIZE_TABLE_MARKER = "\n\n[LOT_SIZE_TABLE]\n"

    def _minimum_risk(self, atr: float) -> float:
        atr_value = max(float(atr), 0.0)
        return max(float(SL_MIN_USD), float(SL_MIN_ATR_MULT) * atr_value)

    ROUND_NUMBER_GRID = 5.0
    ROUND_NUMBER_BUFFER = 0.30

    def _clear_round_number(self, direction: str, sl: float) -> float:
        """Stops parked on $5-grid round numbers get hunted; push them past."""
        nearest = round(sl / self.ROUND_NUMBER_GRID) * self.ROUND_NUMBER_GRID
        if abs(sl - nearest) >= self.ROUND_NUMBER_BUFFER:
            return sl
        if direction == "LONG":
            return nearest - self.ROUND_NUMBER_BUFFER
        return nearest + self.ROUND_NUMBER_BUFFER

    @staticmethod
    def _cap_tp2_at_measured_move(
        direction: str,
        entry: float,
        tp1: float,
        tp2: float,
        zone_dict: dict[str, Any],
        atr: float,
    ) -> float:
        """Cap TP2 at the measured move of the prior leg, shaved by 0.1 ATR
        (books: markets stall just before the full projection)."""
        raw_move = zone_dict.get("measured_move")
        if raw_move is None:
            return tp2
        try:
            move = float(raw_move)
        except (TypeError, ValueError):
            return tp2
        if move <= 0:
            return tp2

        shave = 0.1 * max(float(atr), 0.0)
        projected = move - shave
        if projected <= 0:
            return tp2

        if direction == "LONG":
            cap = entry + projected
            if tp1 < cap < tp2:
                return cap
            return tp2
        cap = entry - projected
        if tp2 < cap < tp1:
            return cap
        return tp2

    def calculate_parameters(
        self,
        trade_direction: str,
        zone_dict: dict[str, Any],
        atr: float,
    ) -> tuple[float, float, float, float]:
        direction = trade_direction.upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError(f"Unsupported trade direction: {trade_direction}")

        if "entry_price" in zone_dict and "sl_price" in zone_dict:
            entry = float(zone_dict["entry_price"])
            sl = float(zone_dict["sl_price"])
        else:
            price_top = float(zone_dict["price_top"])
            price_bottom = float(zone_dict["price_bottom"])
            atr_value = float(atr)

            if direction == "LONG":
                entry = price_top
                sl = price_bottom - (ATR_SL_MULTIPLIER * atr_value)
            else:
                entry = price_bottom
                sl = price_top + (ATR_SL_MULTIPLIER * atr_value)

        # Enforce a minimum stop distance so normal noise cannot wick out the trade.
        min_risk = self._minimum_risk(atr)
        sl = self._clear_round_number(direction, sl)
        if direction == "LONG":
            risk = entry - sl
            if 0 < risk < min_risk:
                sl = entry - min_risk
                risk = min_risk
            tp1 = entry + (1.5 * risk)
            tp2 = entry + (3.0 * risk)
        else:
            risk = sl - entry
            if 0 < risk < min_risk:
                sl = entry + min_risk
                risk = min_risk
            tp1 = entry - (1.5 * risk)
            tp2 = entry - (3.0 * risk)

        if risk <= 0:
            raise ValueError("Signal risk must be positive")

        tp2 = self._cap_tp2_at_measured_move(direction, entry, tp1, tp2, zone_dict, atr)

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

        confluence_notes = zone_dict.get("confluence_notes")
        if isinstance(confluence_notes, list) and confluence_notes:
            rendered_notes = "\n".join(f"- {note}" for note in confluence_notes)
            base_reasoning = f"{base_reasoning}\n{rendered_notes}"

        lot_size_table = LotSizeCalculator().generate_table(entry, sl)
        reasoning = f"{base_reasoning}{self.LOT_SIZE_TABLE_MARKER}{lot_size_table}"

        zone_id = zone_dict.get("id", zone_dict.get("zone_id"))
        strategy_key: Optional[str] = zone_dict.get("strategy")
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

        order_type = str(zone_dict.get("order_type", "LIMIT")).upper()
        if order_type not in {"STOP", "LIMIT"}:
            order_type = "LIMIT"

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
            order_type=order_type,
            strategy=str(strategy_key) if strategy_key is not None else None,
        )
