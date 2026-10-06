"""Realized result of a trade in R (1R = the money risked on the trade).

Trade plan: half the position is banked at TP1 (1.5R), the rest runs to TP2
or is protected at entry. Every report, the website and the dashboard read the
exact value stored at closure, so a trade stopped at entry before TP1 counts
0R (it used to share the CLOSED_BE status, and +0.75R, with a runner closed at
entry after TP1).
"""

from __future__ import annotations

from typing import Any, Optional

TP1_R = 1.5
TP1_SHARE = 0.5
BANKED_AT_TP1 = TP1_R * TP1_SHARE  # 0.75R

CLOSED_STATUSES = (
    "CLOSED_TP2",
    "CLOSED_BE",
    "CLOSED_SL",
    "CLOSED_TIME",
    "CLOSED_STRUCT",
    "CLOSED_WEEKEND",
)

# Only for rows written before realized_r existed and without enough detail.
STATUS_R_FALLBACK = {
    "CLOSED_TP2": 2.25,
    "CLOSED_BE": 0.75,
    "CLOSED_SL": -1.0,
    "CLOSED_TIME": 0.0,
    "CLOSED_STRUCT": 1.0,
    "CLOSED_WEEKEND": 0.0,
}

EARLY_BE_MARKER = "then returned to entry"


def _risk(entry: float, sl: float) -> float:
    return abs(float(entry) - float(sl))


def _move_r(direction: str, entry: float, price: float, risk: float) -> float:
    move = float(price) - float(entry)
    if str(direction).upper() == "SHORT":
        move = -move
    return move / risk


def realized_r_for_event(
    event_type: str,
    direction: str,
    entry: float,
    sl: float,
    tp2: float,
    exit_price: Optional[float] = None,
    tp1: Optional[float] = None,
) -> Optional[float]:
    """Exact R for a closing lifecycle event; None for events that do not close a trade.

    `tp1` gives the half banked at TP1 from the real level (spread cushions and
    tested target settings move it); without it the classic 1.5R is assumed."""
    risk = _risk(entry, sl)
    if risk <= 0:
        return None
    banked = BANKED_AT_TP1
    if tp1 is not None:
        try:
            banked = TP1_SHARE * _move_r(direction, entry, float(tp1), risk)
        except (TypeError, ValueError):
            banked = BANKED_AT_TP1
    event = str(event_type).upper()
    if event == "SL_HIT":
        return -1.0
    if event == "EARLY_BE":
        return 0.0
    if event == "BE_HIT":
        return round(banked, 4)
    if event == "TP2_SMASH":
        return round(banked + TP1_SHARE * _move_r(direction, entry, tp2, risk), 4)
    if event in {"TIME_STOP", "WEEKEND_CLOSE"}:
        if exit_price is None:
            return 0.0
        return round(_move_r(direction, entry, exit_price, risk), 4)
    if event in {"STRUCTURE_EXIT", "WEEKEND_RUNNER_CLOSE"}:
        if exit_price is None:
            return round(banked, 4)
        return round(banked + TP1_SHARE * _move_r(direction, entry, exit_price, risk), 4)
    return None


def derive_realized_r(
    status: Any,
    closure_reason: Any,
    direction: Any,
    entry: Any,
    sl: Any,
    tp2: Any,
) -> Optional[float]:
    """Best reconstruction for rows closed before realized_r was stored."""
    state = str(status or "").upper()
    if state not in CLOSED_STATUSES:
        return None
    reason = str(closure_reason or "")
    if state == "CLOSED_BE":
        return 0.0 if EARLY_BE_MARKER in reason else BANKED_AT_TP1
    if state == "CLOSED_TP2":
        try:
            value = realized_r_for_event(
                "TP2_SMASH", str(direction), float(entry), float(sl), float(tp2)
            )
        except (TypeError, ValueError):
            value = None
        return value if value is not None else STATUS_R_FALLBACK[state]
    return STATUS_R_FALLBACK[state]


def row_r(status: Any, realized_r: Any) -> Optional[float]:
    """Stored exact value when present, legacy status value otherwise."""
    if realized_r is not None:
        try:
            return float(realized_r)
        except (TypeError, ValueError):
            pass
    return STATUS_R_FALLBACK.get(str(status or "").upper())
