"""Instrument registry: per-symbol market personality.

Every engine that previously assumed gold's price scale (dollar grids,
2-decimal rounding, weekend closures, NY-anchored sessions) reads its
parameters from here instead. Unknown symbols fall back to the gold
profile so existing behaviour (and tests) are unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


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
    spot_source: str = ""  # "TWELVEDATA" = broker-style spot feed first, Yahoo as backup
    spot_symbol: str = ""  # symbol name at the spot source, e.g. XAU/USD
    typical_spread: float = 0.0  # normal broker spread in price units
    slippage: float = 0.0  # extra price lost per fill in history tests
    news_currencies: tuple[str, ...] = ("USD",)  # whose high-impact news matters
    usd_exposure: int = 0  # LONG = -1 short dollar, +1 long dollar, 0 = not a dollar bet
    code_prefix: str = ""  # short signal code, e.g. G -> #G142
    trial: bool = False  # True = signals go to the owner's chat only
    history_source: str = ""  # "HISTDATA" | "BINANCE": free multi-year history for tests
    history_symbol: str = ""  # name at the history source
    base_symbol: str = ""  # swing versions share the real market's prices and history
    cash_session: bool = False  # prices only in the New York cash session (US indices)
    # False = no free price feed that is on time: history tests only, never live.
    live_feed: bool = True


def _swing(base: "Instrument", timeframe: str, prefix: str, scale: float) -> "Instrument":
    """Slower signals on the same market: own chart timeframe, own memory
    (zones, swings), wider distances, starting in trial."""
    label = {"H1": "1-hour swing", "H4": "4-hour swing"}[timeframe]
    return replace(
        base,
        symbol=f"{base.symbol}_{timeframe}",
        display_name=f"{base.display_name} {label}",
        signal_timeframe=timeframe,
        min_stop_abs=base.min_stop_abs * scale,
        entry_buffer=base.entry_buffer * scale,
        zone_proximity=base.zone_proximity * scale,
        pivot_tolerance_floor=base.pivot_tolerance_floor * scale,
        london_min_net=base.london_min_net * scale,
        code_prefix=prefix,
        trial=True,
        base_symbol=base.symbol,
    )


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
        # Spot XAU/USD has no traded volume; sweeps are judged on price alone.
        requires_volume=False,
        spot_source="TWELVEDATA",
        spot_symbol="XAU/USD",
        typical_spread=0.30,
        slippage=0.10,
        news_currencies=("USD",),
        usd_exposure=-1,
        code_prefix="G",
        history_source="HISTDATA",
        history_symbol="XAUUSD",
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
        typical_spread=20.0,
        slippage=5.0,
        news_currencies=("USD",),
        code_prefix="B",
        history_source="BINANCE",
        history_symbol="BTCUSDT",
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
        typical_spread=0.00008,
        slippage=0.00002,
        news_currencies=("USD", "EUR"),
        usd_exposure=-1,
        code_prefix="E",
        history_source="HISTDATA",
        history_symbol="EURUSD",
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
        typical_spread=0.00012,
        slippage=0.00003,
        news_currencies=("USD", "GBP"),
        usd_exposure=-1,
        code_prefix="P",
        history_source="HISTDATA",
        history_symbol="GBPUSD",
    ),
    # --- Trial markets: signals go to the owner's chat until they prove themselves ---
    "XAGUSD": Instrument(
        symbol="XAGUSD",
        display_name="Silver",
        # Yahoo's silver is futures, 10 minutes late; TwelveData spot silver is paid.
        yahoo_ticker="SI=F",
        asset_class="metal",
        price_decimals=3,
        round_grid=0.50,
        round_buffer=0.03,
        min_stop_abs=0.09,
        pip_size=0.01,
        pip_value_per_lot=50.0,
        lot_note="1.00 lot = 5,000 oz = $50 per 0.01 move (check your broker)",
        weekend_trading=False,
        session_scored=True,
        macro_gold_filters=False,
        pivot_roll="ny17",
        pivot_tolerance_floor=0.03,
        london_min_net=0.015,
        requires_volume=False,
        entry_buffer=0.015,
        zone_proximity=0.03,
        typical_spread=0.025,
        slippage=0.005,
        news_currencies=("USD",),
        usd_exposure=-1,
        code_prefix="S",
        trial=True,
        history_source="HISTDATA",
        history_symbol="XAGUSD",
        live_feed=False,
    ),
    "US100": Instrument(
        symbol="US100",
        display_name="US100 (Nasdaq)",
        yahoo_ticker="^NDX",  # the cash index, live during the New York session
        asset_class="index",
        price_decimals=1,
        round_grid=50.0,
        round_buffer=3.0,
        min_stop_abs=24.0,
        pip_size=1.0,
        pip_value_per_lot=1.0,
        lot_note="1.00 lot = $1 per point (many brokers; check yours)",
        weekend_trading=False,
        session_scored=False,
        macro_gold_filters=False,
        pivot_roll="ny17",
        pivot_tolerance_floor=8.0,
        london_min_net=4.0,
        requires_volume=False,
        entry_buffer=4.0,
        zone_proximity=8.0,
        typical_spread=1.5,
        slippage=0.5,
        news_currencies=("USD",),
        code_prefix="N",
        trial=True,
        history_source="HISTDATA",
        history_symbol="NSXUSD",
        cash_session=True,
    ),
    # The Dow (US30) has no free multi-year history to test on; the S&P 500 does.
    "US500": Instrument(
        symbol="US500",
        display_name="US500 (S&P 500)",
        yahoo_ticker="^GSPC",
        asset_class="index",
        price_decimals=1,
        round_grid=25.0,
        round_buffer=1.0,
        min_stop_abs=5.0,
        pip_size=1.0,
        pip_value_per_lot=1.0,
        lot_note="1.00 lot = $1 per point (many brokers; check yours)",
        weekend_trading=False,
        session_scored=False,
        macro_gold_filters=False,
        pivot_roll="ny17",
        pivot_tolerance_floor=1.7,
        london_min_net=0.8,
        requires_volume=False,
        entry_buffer=0.8,
        zone_proximity=1.7,
        typical_spread=0.5,
        slippage=0.2,
        news_currencies=("USD",),
        code_prefix="U",
        trial=True,
        history_source="HISTDATA",
        history_symbol="SPXUSD",
        cash_session=True,
    ),
    "USDJPY": Instrument(
        symbol="USDJPY",
        display_name="Dollar/Yen",
        yahoo_ticker="JPY=X",
        asset_class="fx",
        price_decimals=3,
        round_grid=0.50,
        round_buffer=0.03,
        min_stop_abs=0.08,
        pip_size=0.01,
        pip_value_per_lot=6.3,
        lot_note="1.00 lot = about $6.30 per pip at 158 (changes with the price)",
        weekend_trading=False,
        session_scored=True,
        macro_gold_filters=False,
        pivot_roll="ny17",
        pivot_tolerance_floor=0.04,
        london_min_net=0.08,
        requires_volume=False,
        entry_buffer=0.02,
        zone_proximity=0.15,
        typical_spread=0.010,
        slippage=0.003,
        news_currencies=("USD", "JPY"),
        usd_exposure=1,
        code_prefix="J",
        trial=True,
        history_source="HISTDATA",
        history_symbol="USDJPY",
    ),
    "AUDUSD": Instrument(
        symbol="AUDUSD",
        display_name="Aussie dollar",
        yahoo_ticker="AUDUSD=X",
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
        typical_spread=0.00012,
        slippage=0.00003,
        news_currencies=("USD", "AUD"),
        usd_exposure=-1,
        code_prefix="A",
        trial=True,
        history_source="HISTDATA",
        history_symbol="AUDUSD",
    ),
    "WTIUSD": Instrument(
        symbol="WTIUSD",
        display_name="Oil (WTI)",
        # Yahoo's oil is futures, 10 minutes late; TwelveData spot oil is paid.
        yahoo_ticker="CL=F",
        asset_class="energy",
        price_decimals=2,
        round_grid=1.0,
        round_buffer=0.05,
        min_stop_abs=0.20,
        pip_size=0.01,
        pip_value_per_lot=10.0,
        lot_note="1.00 lot = 1,000 barrels = $10 per 0.01 move (check your broker)",
        weekend_trading=False,
        session_scored=True,
        macro_gold_filters=False,
        pivot_roll="ny17",
        pivot_tolerance_floor=0.07,
        london_min_net=0.03,
        requires_volume=False,
        entry_buffer=0.03,
        zone_proximity=0.07,
        signal_timeframe="M15",
        typical_spread=0.03,
        slippage=0.01,
        news_currencies=("USD",),
        code_prefix="O",
        trial=True,
        history_source="HISTDATA",
        history_symbol="WTIUSD",
        live_feed=False,
    ),
    "ETHUSD": Instrument(
        symbol="ETHUSD",
        display_name="Ethereum",
        yahoo_ticker="ETH-USD",
        asset_class="crypto",
        price_decimals=2,
        round_grid=50.0,
        round_buffer=2.0,
        min_stop_abs=6.0,
        pip_size=1.0,
        pip_value_per_lot=1.0,
        lot_note="1.00 lot = 1 ETH | $1 per $1 move",
        weekend_trading=True,
        session_scored=False,
        macro_gold_filters=False,
        pivot_roll="utc0",
        pivot_tolerance_floor=1.6,
        london_min_net=4.0,
        requires_volume=False,
        entry_buffer=1.0,
        zone_proximity=12.0,
        signal_timeframe="M15",
        typical_spread=2.0,
        slippage=0.5,
        news_currencies=("USD",),
        code_prefix="H",
        trial=True,
        history_source="BINANCE",
        history_symbol="ETHUSDT",
    ),
}

# Slower swing signals for people who cannot watch every 5 minutes.
for _base, _timeframe, _prefix, _scale in (
    ("XAUUSD", "H1", "GH", 3.5),
    ("XAUUSD", "H4", "GF", 7.0),
    ("EURUSD", "H1", "EH", 3.5),
    ("GBPUSD", "H1", "PH", 3.5),
):
    _swing_instrument = _swing(INSTRUMENTS[_base], _timeframe, _prefix, _scale)
    INSTRUMENTS[_swing_instrument.symbol] = _swing_instrument

# The gold 4-hour chart runs the Gold 4-hour System (src/strategies/gold_system.py),
# which passed 3 years of history after costs, so it posts to subscribers.
INSTRUMENTS["XAUUSD_H4"] = replace(
    INSTRUMENTS["XAUUSD_H4"], display_name="Gold 4-hour system", trial=False
)

_DEFAULT = INSTRUMENTS["XAUUSD"]


def get_instrument(symbol: str | None) -> Instrument:
    """Registry lookup; unknown/blank symbols behave like gold (legacy)."""
    if not symbol:
        return _DEFAULT
    return INSTRUMENTS.get(str(symbol).upper(), _DEFAULT)


# Gold only: 5-minute prices (watching open trades) and the 4-hour system chart.
DEFAULT_SYMBOLS = "XAUUSD,XAUUSD_H4"


def active_symbols() -> list[str]:
    """Symbols the pulse trades, from the SYMBOLS env (comma-separated).
    Default: gold (DEFAULT_SYMBOLS)."""
    raw = os.getenv("SYMBOLS") or DEFAULT_SYMBOLS
    seen: list[str] = []
    for part in raw.split(","):
        name = part.strip().upper()
        if name and name not in seen:
            seen.append(name)
    return seen or ["XAUUSD"]


ACTIVE_SYMBOLS: list[str] = active_symbols()


def history_key(symbol: str | None) -> str:
    """Name the stored history files use: swing versions share the real market's."""
    instrument = get_instrument(symbol)
    return instrument.base_symbol or instrument.symbol


def _env_set(name: str) -> set[str]:
    return {part.strip().upper() for part in (os.getenv(name) or "").split(",") if part.strip()}


# New strategies start in trial too. GitHub variable TRIAL_STRATEGIES replaces
# this list; the value NONE promotes every strategy.
DEFAULT_TRIAL_STRATEGIES = "OPENING_RANGE_BREAKOUT,ASIAN_RANGE_BREAKOUT"


def is_trial(symbol: str | None, strategy: str | None = None) -> bool:
    """Trial signals go to the owner's chat only and are kept out of the
    public results until promoted (GitHub variables PROMOTED_SYMBOLS and
    TRIAL_STRATEGIES)."""
    instrument = get_instrument(symbol)
    if instrument.trial and instrument.symbol not in _env_set("PROMOTED_SYMBOLS"):
        return True
    raw = (os.getenv("TRIAL_STRATEGIES") or DEFAULT_TRIAL_STRATEGIES).strip().upper()
    trial_strategies = set() if raw == "NONE" else {s.strip() for s in raw.split(",") if s.strip()}
    return bool(strategy) and str(strategy).upper() in trial_strategies


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
