import os
from pathlib import Path

# pyre-ignore[21]
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

LOCK_FILE_PATH = str(BASE_DIR / "data" / "bot.lock")
LOG_FILE_PATH = str(BASE_DIR / "logs" / "daily-run.log")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
TWELVEDATA_BASE_URL = os.getenv("TWELVEDATA_BASE_URL", "https://api.twelvedata.com")

SYMBOL_MAP = {"XAUUSD": "XAU/USD"}
YAHOO_SYMBOL_MAP = {"XAUUSD": "GC=F"}

TIMEFRAME_SECONDS = {
	"M1": 60,
	"M5": 300,
	"M15": 900,
	"M30": 1800,
	"H1": 3600,
	"H2": 7200,
	"H4": 14400,
	"H6": 21600,
	"H8": 28800,
	"H12": 43200,
	"D": 86400,
	"W": 604800,
}

# --- Sovereign Demand Proxy ---
SOVEREIGN_ACCUMULATION_THRESHOLD = 350.0  # tonnes per quarter
LONG_BIAS_MULTIPLIER_ACTIVE = 1.25
LONG_BIAS_MULTIPLIER_INACTIVE = 1.0

# --- Crisis Filter (DXY) ---
# Trigger linter
DXY_TICKER = 'DX-Y.NYB'
DXY_CORRELATION_WINDOW = 20

# --- Commitment of Traders (COT) ---
COT_LOOKBACK_WEEKS = 26
COT_OVERCROWDED_THRESHOLD = 80.0
COT_CAPITULATION_THRESHOLD = 20.0

# --- Consensus Variance (Surprise Factor) ---
SURPRISE_FACTOR_THRESHOLD = 2.0
HIGH_IMPACT_EVENTS = [
    {"event_name": "NFP", "forecast": 180.0, "actual": -50.0, "historical_sigma": 45.0, "usd_impact_direction": 1},
    {"event_name": "CPI", "forecast": 0.3, "actual": 0.5, "historical_sigma": 0.1, "usd_impact_direction": 1},
    {"event_name": "FOMC", "forecast": -0.25, "actual": 0.25, "historical_sigma": 0.15, "usd_impact_direction": -1},
]

# --- Fundamental Shift Rate (FSR) ---
FSR_LOOKBACK_PERIOD = 20
FSR_HIGH_MOMENTUM_THRESHOLD = 0.5
FSR_MEAN_REVERSION_THRESHOLD = -0.5

# --- Macro-Bias Aggregation ---
SCORE_CRISIS_MODE = 25
SCORE_COT_BULLISH = 20
SCORE_COT_BEARISH = -20
SCORE_CONSENSUS_BULLISH = 30
SCORE_CONSENSUS_BEARISH = -30

BIAS_LONG_THRESHOLD = 25
BIAS_SHORT_THRESHOLD = -25
