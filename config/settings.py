import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
LOCK_FILE_PATH = str(BASE_DIR / "data" / "bot.lock")
LOG_FILE_PATH = str(BASE_DIR / "logs" / "daily-run.log")

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_BASE_URL = os.getenv("OANDA_BASE_URL", "https://api-fxpractice.oanda.com")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
TWELVEDATA_BASE_URL = os.getenv("TWELVEDATA_BASE_URL", "https://api.twelvedata.com")

SYMBOL_MAP = {"XAUUSD": "XAU/USD"}
