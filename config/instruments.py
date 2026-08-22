"""Instrument registry: per-symbol market personality.

Every engine that previously assumed gold's price scale (dollar grids,
2-decimal rounding, weekend closures, NY-anchored sessions) reads its
parameters from here instead. Unknown symbols fall back to the gold
profile so existing behaviour (and tests) are unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    display_name: str
    yahoo_ticker: str
    asset_class: str  # "metal" | "crypto" | "fx"
    price_decimals: int
    round_grid: float  # psychological round-number grid for stop placement
    round_buffer: float  # push stops this far past a round number
    min_stop_abs: float  # absolute floor for stop distance
    pip_size: float  # 1 pip in price units
    pip_value_per_lot: float  # account-currency value of 1 pip per 1.00 lot
    lot_note: str  # human explanation for the sizing table
    weekend_trading: bool  # True = 24/7 market (crypto)
    session_scored: bool  # False = no killzone penalty (24/7 markets)
    macro_gold_filters: bool  # COT/sovereign/SMT gold-macro gates apply
    pivot_roll: str  # "ny17" (futures/FX day) or "utc0" (crypto day)
    pivot_tolerance_floor: float  # min price distance for pivot confluence
    london_min_net: float  # min London net move to call a direction
    requires_volume: bool  # Yahoo FX feeds report volume=0
    entry_buffer: float = 0.50  # stop-order offset beyond the trigger bar
    zone_proximity: float = 1.00  # "close enough to the zone" distance
    signal_timeframe: str = ""  # override; empty = global SIGNAL_TIMEFRAME
    signals_enabled: bool = True  # False = watch-only (data + zones, no signals)
    correlation_group: str = ""  # same group + same direction = doubled bet


INSTRUMENTS: dict[str, Instrument] = {
    "XAUUSD": Instrument(
        symbol="XAUUSD",
        display_name="Gold",
        yahoo_ticker="GC=F",
        asset_class="metal",
        price_decimals=2,
        round_grid=5.0,
        round_buffer=0.30,
        min_stop_abs=3.0,
        pip_size=0.10,
        pip_value_per_lot=10.0,
        lot_note="1.00 lot = $10 per pip",
        weekend_trading=False,
        session_scored=True,
        macro_gold_filters=True,
        pivot_roll="ny17",
        pivot_tolerance_floor=1.0,
        london_min_net=0.5,
        requires_volume=True,
    ),
    "BTCUSD": Instrument(
        symbol="BTCUSD",
        display_name="Bitcoin",
        yahoo_ticker="BTC-USD",
        asset_class="crypto",
        price_decimals=1,
        round_grid=1000.0,
        round_buffer=50.0,
        min_stop_abs=150.0,
        pip_size=1.0,
        pip_value_per_lot=1.0,
        lot_note="1.00 lot = 1 BTC | $1 per $1 move",
        weekend_trading=True,
        session_scored=False,
        macro_gold_filters=False,
        pivot_roll="utc0",
        pivot_tolerance_floor=40.0,
        london_min_net=100.0,
        requires_volume=True,
        entry_buffer=25.0,
        zone_proximity=300.0,
        # M5 replay: -16.5R/45d, 0% full wins — BTC chop needs slower bars.
        signal_timeframe="M15",
    ),
    "EURUSD": Instrument(
        symbol="EURUSD",
        display_name="Euro",
        yahoo_ticker="EURUSD=X",
        asset_class="fx",
        price_decimals=5,
        round_grid=0.0050,
        round_buffer=0.0003,
        min_stop_abs=0.0008,
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_note="1.00 lot = $10 per pip",
        weekend_trading=False,
        session_scored=True,
        macro_gold_filters=False,
        pivot_roll="ny17",
        pivot_tolerance_floor=0.0004,
        london_min_net=0.0008,
        requires_volume=False,
        entry_buffer=0.0002,
        zone_proximity=0.0015,
        correlation_group="EUR_GBP_BLOC",
    ),
    "GBPUSD": Instrument(
        symbol="GBPUSD",
        display_name="Pound",
        yahoo_ticker="GBPUSD=X",
        asset_class="fx",
        price_decimals=5,
        round_grid=0.0050,
        round_buffer=0.0003,
        min_stop_abs=0.0010,
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_note="1.00 lot = $10 per pip",
        weekend_trading=False,
        session_scored=True,
        macro_gold_filters=False,
        pivot_roll="ny17",
        pivot_tolerance_floor=0.0005,
        london_min_net=0.0010,
        requires_volume=False,
        entry_buffer=0.0002,
        zone_proximity=0.0018,
        correlation_group="EUR_GBP_BLOC",
    ),
}

_DEFAULT = INSTRUMENTS["XAUUSD"]


def get_instrument(symbol: str | None) -> Instrument:
    """Registry lookup; unknown/blank symbols behave like gold (legacy)."""
    if not symbol:
        return _DEFAULT
    return INSTRUMENTS.get(str(symbol).upper(), _DEFAULT)


def active_symbols() -> list[str]:
    """Symbols the pulse trades, from the SYMBOLS env (comma-separated)."""
    raw = os.getenv("SYMBOLS", "XAUUSD,BTCUSD,EURUSD,GBPUSD")
    seen: list[str] = []
    for part in raw.split(","):
        name = part.strip().upper()
        if name and name not in seen:
            seen.append(name)
    return seen or ["XAUUSD"]


ACTIVE_SYMBOLS: list[str] = active_symbols()


def state_key(base: str, symbol: str | None) -> str:
    """Per-symbol kv key. XAUUSD keeps the legacy unsuffixed keys so live
    state and old dashboards survive the multi-symbol migration."""
    if not symbol or str(symbol).upper() == "XAUUSD":
        return base
    return f"{base}:{str(symbol).upper()}"


def scaled_buffer(symbol: str | None, legacy_value: float) -> float:
    """Entry buffer for a market; gold keeps the ctor/env legacy value so
    existing tests and tuned behavior are untouched."""
    instrument = get_instrument(symbol)
    if instrument.symbol == "XAUUSD":
        return float(legacy_value)
    return instrument.entry_buffer


def scaled_proximity(symbol: str | None, legacy_value: float) -> float:
    """Zone-proximity distance for a market; gold keeps the legacy value."""
    instrument = get_instrument(symbol)
    if instrument.symbol == "XAUUSD":
        return float(legacy_value)
    return instrument.zone_proximity
