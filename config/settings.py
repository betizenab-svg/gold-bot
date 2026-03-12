import os
from pathlib import Path

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
