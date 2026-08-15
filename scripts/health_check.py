from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import requests
import yfinance as yf

from config.settings import BASE_DIR, TELEGRAM_BOT_TOKEN


def _production_db_path() -> Path:
    return Path(BASE_DIR) / "data" / "trading_engine.db"


def check_db_integrity() -> bool:
    db_path = _production_db_path()
    if not db_path.exists():
        return False
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("PRAGMA integrity_check;").fetchone()
    return row is not None and str(row[0]).lower() == "ok"


def check_yahoo() -> bool:
    frame = yf.download(
        tickers="GC=F",
        period="1d",
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    return frame is not None and not frame.empty


def check_telegram() -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN or ''}/getMe"
    response = requests.get(url, timeout=15)
    return response.status_code == 200


def main() -> int:
    checks = [
        ("DB integrity", check_db_integrity),
        ("Yahoo API", check_yahoo),
        ("Telegram API", check_telegram),
    ]

    all_ok = True
    for label, fn in checks:
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        status = "[OK]" if ok else "[FAIL]"
        print(f"{status} {label}")
        if not ok:
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
