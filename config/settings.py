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
TELEGRAM_API_BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")

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
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", "1.5"))
VALUE_AREA_SMA = 20
PIN_BAR_TAIL_RATIO = 0.66
ENTRY_BUFFER_PTS = 0.50
INSIDE_BAR_LOOKBACK_CANDLES = 3

# --- Signal Quality & Risk (v2) ---
# Timeframe the strategy engines run on (M1 is too noisy for delayed feeds).
SIGNAL_TIMEFRAME = os.getenv("SIGNAL_TIMEFRAME", "M5")
# Minimum stop distance: never risk less than max(SL_MIN_USD, SL_MIN_ATR_MULT * ATR).
SL_MIN_USD = float(os.getenv("SL_MIN_USD", "3.0"))
SL_MIN_ATR_MULT = float(os.getenv("SL_MIN_ATR_MULT", "1.0"))
# Pending signals that never trigger are cancelled after this window.
SIGNAL_EXPIRY_MINUTES = int(os.getenv("SIGNAL_EXPIRY_MINUTES", "90"))
# Candle history pulled for the intelligence engines each pulse.
ANALYSIS_LOOKBACK_CANDLES = int(os.getenv("ANALYSIS_LOOKBACK_CANDLES", "240"))
# Attach a rendered chart image to each Telegram signal.
CHART_ALERTS_ENABLED = _env_bool("CHART_ALERTS_ENABLED", True)
# Days of market_data retained in SQLite (keeps the DB small on free hosting).
MARKET_DATA_RETENTION_DAYS = int(os.getenv("MARKET_DATA_RETENTION_DAYS", "45"))

# --- Risk Governor ---
RISK_MAX_SIGNALS_PER_DAY = int(os.getenv("RISK_MAX_SIGNALS_PER_DAY", "6"))
RISK_SL_COOLDOWN_MINUTES = int(os.getenv("RISK_SL_COOLDOWN_MINUTES", "45"))
RISK_CONSECUTIVE_SL_HALT = int(os.getenv("RISK_CONSECUTIVE_SL_HALT", "3"))
RISK_HALT_HOURS = int(os.getenv("RISK_HALT_HOURS", "6"))
RISK_MAX_CONCURRENT_SIGNALS = int(os.getenv("RISK_MAX_CONCURRENT_SIGNALS", "2"))
# Escalation tier: this many consecutive stop losses suspends signals for 24h.
RISK_TIER2_CONSECUTIVE_SL = int(os.getenv("RISK_TIER2_CONSECUTIVE_SL", "5"))
# Daily realized-R circuit breakers (Link/Kiev/Bennett consensus).
RISK_DAILY_MAX_LOSS_R = float(os.getenv("RISK_DAILY_MAX_LOSS_R", "3.0"))
RISK_DAILY_PROFIT_LOCK_R = float(os.getenv("RISK_DAILY_PROFIT_LOCK_R", "4.0"))
# High-impact news blackout window in minutes (before/after event).
NEWS_BLACKOUT_BEFORE_MIN = int(os.getenv("NEWS_BLACKOUT_BEFORE_MIN", "30"))
NEWS_BLACKOUT_AFTER_MIN = int(os.getenv("NEWS_BLACKOUT_AFTER_MIN", "15"))
# Automatic calendar sync (ForexFactory weekly JSON feed, keyless).
NEWS_AUTOFETCH_ENABLED = _env_bool("NEWS_AUTOFETCH_ENABLED", True)
NEWS_CALENDAR_URL = os.getenv(
    "NEWS_CALENDAR_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
)
NEWS_CALENDAR_REFRESH_HOURS = int(os.getenv("NEWS_CALENDAR_REFRESH_HOURS", "12"))
# Automatic weekly performance report to Telegram.
WEEKLY_REPORT_ENABLED = _env_bool("WEEKLY_REPORT_ENABLED", True)
WEEKLY_REPORT_INTERVAL_DAYS = int(os.getenv("WEEKLY_REPORT_INTERVAL_DAYS", "7"))
WEEKLY_REPORT_MIN_TRADES = int(os.getenv("WEEKLY_REPORT_MIN_TRADES", "10"))
# Time stop: ACTIVE trades that never reached TP1 are closed after this.
ACTIVE_MAX_HOLD_HOURS = int(os.getenv("ACTIVE_MAX_HOLD_HOURS", "24"))
