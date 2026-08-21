from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from config.instruments import get_instrument
from config.settings import (
    ACTIVE_MAX_HOLD_HOURS,
    ATR_SL_MULTIPLIER,
    SIGNAL_EXPIRY_MINUTES,
    SL_MIN_ATR_MULT,
    SL_MIN_USD,
)
from src.analysis.position_sizing import LotSizeCalculator
from src.domain.signal import Signal


class SignalFactory:
    """Construct executable signal objects from actionable setups."""

    LOT_SIZE_TABLE_MARKER = "\n\n[LOT_SIZE_TABLE]\n"

    def _minimum_risk(self, atr: float, symbol: str = "XAUUSD") -> float:
        atr_value = max(float(atr), 0.0)
        instrument = get_instrument(symbol)
        # Gold keeps the legacy env override; other markets use their own floor.
        floor = float(SL_MIN_USD) if instrument.symbol == "XAUUSD" else instrument.min_stop_abs
        return max(floor, float(SL_MIN_ATR_MULT) * atr_value)

    ROUND_NUMBER_GRID = 5.0
    ROUND_NUMBER_BUFFER = 0.30

    def _clear_round_number(
        self, direction: str, sl: float, symbol: str = "XAUUSD"
    ) -> float:
        """Stops parked on round-number grids get hunted; push them past."""
        instrument = get_instrument(symbol)
        grid = instrument.round_grid
        buffer = instrument.round_buffer
        nearest = round(sl / grid) * grid
        if abs(sl - nearest) >= buffer:
            return sl
        if direction == "LONG":
            return nearest - buffer
        return nearest + buffer

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

    def _render_trade_plan(
        self,
        plan: dict[str, Any],
        zone_dict: dict[str, Any],
        direction: str,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        score: int,
        symbol: str = "XAUUSD",
    ) -> str:
        """Professional trade-plan narrative: thesis, evidence, numbers, and
        pre-committed reactions (Link/Kiev/Bassal journaling consensus)."""
        nd = get_instrument(symbol).price_decimals
        if score >= 85:
            tier = "Tier 1 - full conviction"
        elif score >= 75:
            tier = "Tier 2 - standard"
        else:
            tier = "Tier 3 - marginal"

        strategy = str(zone_dict.get("strategy") or "SMC ZONE").replace("_", " ").title()
        order_type = str(zone_dict.get("order_type", "LIMIT")).upper()
        zone_type = str(zone_dict.get("type", "")).replace("_", " ").strip()
        zone_status = str(zone_dict.get("status", "")).title()

        lines: list[str] = [f"TRADE PLAN | Score {score} | {tier}"]

        context_bits = []
        if plan.get("structure"):
            context_bits.append(f"Structure {plan['structure']}")
        if plan.get("macro_bias"):
            context_bits.append(f"Macro bias {plan['macro_bias']}")
        if plan.get("regime"):
            context_bits.append(f"Regime {plan['regime']}")
        if plan.get("session"):
            context_bits.append(f"Session: {plan['session']}")
        if context_bits:
            lines.append("Context: " + " | ".join(context_bits))

        if zone_type:
            try:
                zone_top = float(zone_dict.get("price_top"))
                zone_bottom = float(zone_dict.get("price_bottom"))
                lines.append(
                    f"Location: {zone_status} {zone_type} "
                    f"{zone_bottom:.{nd}f}-{zone_top:.{nd}f}"
                )
            except (TypeError, ValueError):
                lines.append(f"Location: {zone_status} {zone_type}")

        sweep_line = plan.get("liquidity")
        lines.append(f"Liquidity: {sweep_line if sweep_line else 'no recent sweep on this side'}")

        lines.append(f"Trigger: {strategy} via {order_type} order")

        notes = plan.get("notes")
        if isinstance(notes, list) and notes:
            lines.append("Evidence:")
            for note in notes:
                lines.append(f"- {note}")

        risk = abs(entry - sl)
        lines.append(
            f"Numbers: entry {entry:.{nd}f} ({order_type}) | SL {sl:.{nd}f} "
            f"(structure + ATR floor, round numbers cleared, risk {risk:.{nd}f}) | "
            f"TP1 {tp1:.{nd}f} (1.5R, bank half) | TP2 {tp2:.{nd}f} "
            f"({'measured-move capped' if zone_dict.get('measured_move') else '3R'}) | "
            "blended 2.25R if both targets pay"
        )

        risk_bits = []
        if plan.get("daily_r") is not None:
            risk_bits.append(f"day so far {plan['daily_r']}")
        risk_bits.append("risk fixed 2% per lot table below")
        lines.append("Risk state: " + " | ".join(risk_bits))

        lines.append(
            "Plan: TP1 hit -> bank half, stop to entry. "
            f"No trigger in {int(SIGNAL_EXPIRY_MINUTES)} min -> cancelled. "
            f"No TP1 within {int(ACTIVE_MAX_HOLD_HOURS)}h -> closed flat. "
            f"Thesis invalid on a close beyond {sl:.{nd}f}."
        )

        return "\n".join(lines)

    def calculate_parameters(
        self,
        trade_direction: str,
        zone_dict: dict[str, Any],
        atr: float,
        symbol: str = "XAUUSD",
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
        min_risk = self._minimum_risk(atr, symbol)
        sl = self._clear_round_number(direction, sl, symbol)
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

        nd = get_instrument(symbol).price_decimals
        return (
            round(entry, nd),
            round(sl, nd),
            round(tp1, nd),
            round(tp2, nd),
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
        entry, sl, tp1, tp2 = self.calculate_parameters(signal_type, zone_dict, atr, symbol)
        zone_status = str(zone_dict.get("status", "UNKNOWN")).title()
        zone_type = str(zone_dict.get("type", "ZONE")).replace("_", " ")

        plan_context = zone_dict.get("plan_context")
        if isinstance(plan_context, dict):
            base_reasoning = self._render_trade_plan(
                plan_context, zone_dict, signal_type, entry, sl, tp1, tp2, int(score), symbol
            )
        else:
            base_reasoning = f"Score: {int(score)}. Entry off {zone_status} {zone_type}."
            confluence_notes = zone_dict.get("confluence_notes")
            if isinstance(confluence_notes, list) and confluence_notes:
                rendered_notes = "\n".join(f"- {note}" for note in confluence_notes)
                base_reasoning = f"{base_reasoning}\n{rendered_notes}"

        lot_size_table = LotSizeCalculator().generate_table(
            entry, sl, risk_pct=0.02 if int(score) >= 85 else 0.01, symbol=symbol
        )
        reasoning = f"{base_reasoning}{self.LOT_SIZE_TABLE_MARKER}{lot_size_table}"

        zone_id = zone_dict.get("id", zone_dict.get("zone_id"))
        strategy_key: Optional[str] = zone_dict.get("strategy")
        nd = get_instrument(symbol).price_decimals
        dedupe_target = zone_id
        if dedupe_target is None:
            dedupe_target = (
                f"{strategy_key}|{round(entry, nd):.{nd}f}|{round(sl, nd):.{nd}f}"
                if strategy_key is not None
                else f"{round(entry, nd):.{nd}f}|{round(sl, nd):.{nd}f}"
            )
        # Date from the signal candle (not wall clock) so a pending setup
        # cannot re-fire as a "new" signal across the midnight rollover.
        date_string = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d")
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
