import os

# pyre-ignore[21]
from dotenv import load_dotenv

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
ENV_PATH = os.path.abspath(os.path.join(BASE_DIR, ".env"))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "data"))
LOGS_DIR = os.path.abspath(os.path.join(BASE_DIR, "logs"))

load_dotenv(ENV_PATH)


def _env_bool(name: str, default: bool) -> bool:
	raw = os.getenv(name)
	if raw is None:
		return default
	return raw.strip().lower() in {"1", "true", "yes", "on"}

UAT_MODE = _env_bool("UAT_MODE", False)
UAT_TELEGRAM_CHAT_ID = os.getenv("UAT_TELEGRAM_CHAT_ID")

if UAT_MODE:
	DB_PATH = os.path.abspath(os.path.join(DATA_DIR, "uat_trading_engine.db"))
else:
	DB_PATH = os.path.abspath(os.getenv("DB_PATH", os.path.join(DATA_DIR, "trading_engine.db")))
LOCK_FILE_PATH = os.path.abspath(os.path.join(DATA_DIR, "bot.lock"))
LOG_FILE_PATH = os.path.abspath(os.path.join(LOGS_DIR, "daily-run.log"))

# Primary ingestion path (Yahoo + FRED) is keyless.
# TwelveData is an optional fallback only.
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
TWELVEDATA_BASE_URL = os.getenv("TWELVEDATA_BASE_URL", "https://api.twelvedata.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PROXY_FALLBACK_ENABLED = _env_bool("PROXY_FALLBACK_ENABLED", True)
PROXY_FALLBACK_MAX_PROXIES = int(os.getenv("PROXY_FALLBACK_MAX_PROXIES", "8"))
PROXY_REQUEST_TIMEOUT_SECONDS = int(os.getenv("PROXY_REQUEST_TIMEOUT_SECONDS", "15"))
PROXYSCRAPE_ENDPOINT = os.getenv(
	"PROXYSCRAPE_ENDPOINT",
	"https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=ipport&format=text&timeout=6000",
)

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
DXY_TICKER = 'DX=F'
DXY_CORRELATION_WINDOW = 20

# --- Commitment of Traders (COT) ---
COT_LOOKBACK_WEEKS = 26
COT_OVERCROWDED_THRESHOLD = 80.0
COT_CAPITULATION_THRESHOLD = 20.0

# --- Consensus Variance (Surprise Factor) ---
SURPRISE_FACTOR_THRESHOLD = 2.0

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

# --- Candlestick Strategies ---
ATR_SL_MULTIPLIER = 0.50
VALUE_AREA_SMA = 20
PIN_BAR_TAIL_RATIO = 0.66
ENTRY_BUFFER_PTS = 0.50
INSIDE_BAR_LOOKBACK_CANDLES = 3
