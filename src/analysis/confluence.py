from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.analysis.adaptive_weights import AdaptiveWeightEngine
from src.analysis.market_state import MarketStateEngine
from src.analysis.momentum import MomentumEngine
from src.analysis.mtf_bias import MultiTimeframeBiasEngine
from src.analysis.pivots import PivotPointEngine
from src.analysis.scoring import ScoringEngine
from src.analysis.sessions import SessionEngine
from src.analysis.trendline import TrendlineEngine
from src.analysis.volatility_regime import VolatilityRegimeEngine
from src.domain.candle import Candle


class ConfluenceEngineV2:
    """Second-generation confluence: SMC base score plus independent engines.

    Any engine veto rejects the setup outright. Engine score deltas adjust the
    base SMC score, then the adaptive per-strategy weight scales the result.
    Every contribution is captured as a human-readable note for the alert.
    """

    # ICT/Boroden optimal trade entry band of the last displacement leg.
    OTE_LOW = 0.618
    OTE_HIGH = 0.786
    OTE_BONUS = 8

    def __init__(
        self,
        scoring_engine: Optional[ScoringEngine] = None,
        session_engine: Optional[SessionEngine] = None,
        volatility_engine: Optional[VolatilityRegimeEngine] = None,
        momentum_engine: Optional[MomentumEngine] = None,
        mtf_engine: Optional[MultiTimeframeBiasEngine] = None,
        weight_engine: Optional[AdaptiveWeightEngine] = None,
        market_state_engine: Optional[MarketStateEngine] = None,
        trendline_engine: Optional[TrendlineEngine] = None,
    ) -> None:
        self.scoring_engine = scoring_engine or ScoringEngine()
        self.session_engine = session_engine or SessionEngine()
        self.volatility_engine = volatility_engine or VolatilityRegimeEngine()
        self.momentum_engine = momentum_engine or MomentumEngine()
        self.mtf_engine = mtf_engine or MultiTimeframeBiasEngine()
        self.weight_engine = weight_engine or AdaptiveWeightEngine()
        self.market_state_engine = market_state_engine or MarketStateEngine()
        self.trendline_engine = trendline_engine or TrendlineEngine()
        self.pivot_engine = PivotPointEngine()

    def evaluate(
        self,
        trade_direction: str,
        macro_bias: str,
        current_structure: str,
        zone_dict: Optional[Dict[str, Any]],
        has_recent_sweep: bool,
        recent_candles: List[Candle],
        current_timestamp: int,
        order_type: str = "LIMIT",
        strategy: Optional[str] = None,
        repository: Any = None,
        entry_price: Optional[float] = None,
        last_swing_high: Optional[float] = None,
        last_swing_low: Optional[float] = None,
        second_attempt: bool = False,
        swing_history: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base_score = self.scoring_engine.calculate_total_score(
            trade_direction=trade_direction,
            macro_bias=macro_bias,
            current_structure=current_structure,
            zone_dict=zone_dict,
            has_recent_sweep=has_recent_sweep,
        )

        notes: List[str] = [f"SMC base score: {int(base_score)}"]
        vetoes: List[str] = []
        delta = 0

        session_result = self._run(self.session_engine.evaluate, int(current_timestamp))
        if session_result:
            delta += int(session_result.get("score", 0))
            notes.append(str(session_result.get("note", "")))

        volatility_result = self._run(
            self.volatility_engine.evaluate, recent_candles, order_type
        )
        if volatility_result:
            if volatility_result.get("veto"):
                vetoes.append(str(volatility_result.get("note", "Volatility veto")))
            else:
                delta += int(volatility_result.get("score", 0))
            notes.append(str(volatility_result.get("note", "")))

        momentum_result = self._run(
            self.momentum_engine.evaluate, recent_candles, trade_direction
        )
        if momentum_result:
            if momentum_result.get("veto"):
                momentum_notes = momentum_result.get("notes") or ["Momentum veto"]
                vetoes.append(str(momentum_notes[0]))
            else:
                delta += int(momentum_result.get("score", 0))
            for note in momentum_result.get("notes", []):
                notes.append(str(note))

        mtf_result = self._run(self.mtf_engine.evaluate, recent_candles, trade_direction)
        if mtf_result:
            if mtf_result.get("veto"):
                vetoes.append(str(mtf_result.get("note", "MTF veto")))
            else:
                delta += int(mtf_result.get("score", 0))
            notes.append(str(mtf_result.get("note", "")))

        state_result = self._run(
            self.market_state_engine.evaluate, recent_candles, trade_direction, order_type
        )
        if state_result:
            if state_result.get("veto"):
                state_notes = state_result.get("notes") or ["Market state veto"]
                vetoes.append(str(state_notes[0]))
            for note in state_result.get("notes", []):
                notes.append(str(note))

        if isinstance(recent_candles, list) and recent_candles:
            trendline_result = self._run(
                self.trendline_engine.counter_trend_check,
                trade_direction,
                current_structure,
                swing_history,
                float(recent_candles[-1].close),
                int(current_timestamp),
            )
            if trendline_result:
                if trendline_result.get("veto"):
                    vetoes.append(str(trendline_result.get("note", "Trendline veto")))
                if trendline_result.get("note"):
                    notes.append(str(trendline_result["note"]))

        ote_note = self._ote_bonus(
            trade_direction, entry_price, last_swing_high, last_swing_low
        )
        if ote_note is not None:
            delta += self.OTE_BONUS
            notes.append(ote_note)

        if second_attempt:
            delta += 10
            notes.append("Second attempt at the same level: higher reliability (+10)")

        pivot_result = self._run(
            self.pivot_engine.evaluate,
            recent_candles,
            trade_direction,
            entry_price,
            int(current_timestamp),
        )
        if pivot_result and pivot_result.get("note"):
            delta += int(pivot_result.get("score", 0))
            notes.append(str(pivot_result["note"]))

        continuation_result = self._run(
            self.session_engine.london_continuation,
            recent_candles,
            trade_direction,
            int(current_timestamp),
        )
        if continuation_result and continuation_result.get("note"):
            delta += int(continuation_result.get("score", 0))
            notes.append(str(continuation_result["note"]))

        smt_delta, smt_note = self._smt_adjustment(repository, trade_direction)
        if smt_note:
            delta += smt_delta
            notes.append(smt_note)

        weight = 1.0
        if repository is not None:
            weight_result = self._run(
                self.weight_engine.calculate_weight, repository, strategy
            )
            if weight_result:
                try:
                    weight = float(weight_result.get("weight", 1.0))
                except (TypeError, ValueError):
                    weight = 1.0
                notes.append(str(weight_result.get("note", "")))

        raw_score = (base_score + delta) * weight
        final_score = int(max(0, min(100, round(raw_score))))

        if vetoes:
            classification = "REJECTED"
        else:
            classification = self.scoring_engine.classify_score(final_score)

        return {
            "base_score": int(base_score),
            "score": final_score,
            "classification": classification,
            "vetoes": vetoes,
            "notes": [note for note in notes if note],
            "weight": weight,
        }

    @staticmethod
    def _smt_adjustment(repository: Any, trade_direction: str) -> tuple[int, Optional[str]]:
        """DXY-divergence bias: gold stretched rich vs the dollar favors shorts,
        stretched cheap favors longs (SMT / correlation-reversion consensus)."""
        if repository is None:
            return 0, None
        try:
            state = repository.get_kv("macro_smt_state")
        except Exception:
            return 0, None
        if not isinstance(state, str):
            return 0, None

        direction = str(trade_direction).upper()
        state = state.upper()
        if state == "GOLD_RICH":
            delta = 5 if direction == "SHORT" else -5
            return delta, f"Gold stretched rich vs DXY: {'supports' if delta > 0 else 'penalizes'} this side ({delta:+d})"
        if state == "GOLD_CHEAP":
            delta = 5 if direction == "LONG" else -5
            return delta, f"Gold stretched cheap vs DXY: {'supports' if delta > 0 else 'penalizes'} this side ({delta:+d})"
        return 0, None

    def _ote_bonus(
        self,
        trade_direction: str,
        entry_price: Optional[float],
        last_swing_high: Optional[float],
        last_swing_low: Optional[float],
    ) -> Optional[str]:
        if entry_price is None or last_swing_high is None or last_swing_low is None:
            return None
        try:
            high = float(last_swing_high)
            low = float(last_swing_low)
            entry = float(entry_price)
        except (TypeError, ValueError):
            return None
        leg = high - low
        if leg <= 0:
            return None

        direction = str(trade_direction).upper()
        if direction == "LONG":
            band_top = high - (self.OTE_LOW * leg)
            band_bottom = high - (self.OTE_HIGH * leg)
        else:
            band_bottom = low + (self.OTE_LOW * leg)
            band_top = low + (self.OTE_HIGH * leg)

        if min(band_bottom, band_top) <= entry <= max(band_bottom, band_top):
            return (
                f"Entry sits in the 61.8-78.6% OTE pocket of the last swing (+{self.OTE_BONUS})"
            )
        return None

    @staticmethod
    def _run(func: Any, *args: Any) -> Optional[Dict[str, Any]]:
        try:
            result = func(*args)
        except Exception as exc:
            logging.debug("Confluence engine %s failed: %s", getattr(func, "__qualname__", func), exc)
            return None
        return result if isinstance(result, dict) else None
