from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
LOCK_FILE_PATH = str(BASE_DIR / "data" / "bot.lock")
LOG_FILE_PATH = str(BASE_DIR / "logs" / "daily-run.log")
