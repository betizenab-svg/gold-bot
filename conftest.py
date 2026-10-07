"""Root conftest.py — ensures the project root is always on sys.path for pytest."""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Test-environment defaults. Set BEFORE config.settings is imported anywhere:
# legacy sprint tests pin the M1 pipeline and expect Telegram dispatch to be
# attempted (they patch send_message). The unroutable API base guarantees any
# unpatched dispatch fails fast without touching the network.
os.environ.setdefault("SIGNAL_TIMEFRAME", "M1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")
os.environ.setdefault("TELEGRAM_API_BASE_URL", "http://127.0.0.1:9")
# Chart rendering is covered by dedicated tests; keep pulse tests fast.
os.environ.setdefault("CHART_ALERTS_ENABLED", "0")
# Network-fetching automations are covered by dedicated unit tests.
os.environ.setdefault("NEWS_AUTOFETCH_ENABLED", "0")
os.environ.setdefault("WEEKLY_REPORT_ENABLED", "0")
os.environ.setdefault("DAILY_STATUS_ENABLED", "0")
os.environ.setdefault("AUTO_QUARANTINE_ENABLED", "0")
# Free-proxy sweeps (up to 8 proxies x 15s per request) made the suite take minutes.
os.environ.setdefault("PROXY_FALLBACK_ENABLED", "0")
# Legacy lifecycle tests assert the original 1R breakeven-arm behavior;
# production default is replay-tuned in settings.
os.environ.setdefault("BE_ARM_R", "1.0")
# Legacy suites exercise the single-symbol pipeline; multi-symbol paths have
# their own dedicated tests that override this.
os.environ.setdefault("SYMBOLS", "XAUUSD")
# Test-only dashboard login (production reads its own from .env).
os.environ.setdefault("DASHBOARD_USERNAME", "test-admin")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-password")
# Never reach real spot-price, admin-chat or watchdog services from tests.
os.environ.setdefault("TWELVEDATA_API_KEY", "")
os.environ.setdefault("TELEGRAM_ADMIN_CHAT_ID", "")
os.environ.setdefault("HEALTHCHECK_PING_URL", "")
os.environ.setdefault("TELEGRAM_VIP_CHAT_ID", "")
os.environ.setdefault("PARTNER_CHAT_IDS", "")
# Tests must not depend on the committed history evidence (quiet hours etc.).
os.environ.setdefault("EVIDENCE_ENABLED", "0")
# Legacy suites pin exact chart-level prices and weekday timings; the spread
# cushion and the Friday weekend plan have their own dedicated tests.
os.environ.setdefault("SPREAD_CUSHION_ENABLED", "0")
os.environ.setdefault("WEEKEND_ACTION", "off")
os.environ.setdefault("MONTHLY_RISK_REVIEW_ENABLED", "0")
os.environ.setdefault("TREND_DAY_FILTER", "off")
# Legacy suites test the older strategies; the gold system has its own tests.
os.environ.setdefault("GOLD_SYSTEM_ONLY", "0")
# Subscriber posts (briefing, pause notices, weekly card/lesson) have their own tests.
for _post in ("MORNING_BRIEFING_ENABLED", "PAUSE_NOTICES_ENABLED", "WEEKLY_CARD_ENABLED", "WEEKLY_LESSON_ENABLED"):
    os.environ.setdefault(_post, "0")
