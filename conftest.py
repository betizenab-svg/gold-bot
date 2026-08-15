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
