from __future__ import annotations

import sqlite3
from pathlib import Path

from config.settings import BASE_DIR


def _production_db_path() -> Path:
    return Path(BASE_DIR) / "data" / "trading_engine.db"


def _lock_path() -> Path:
    return Path(BASE_DIR) / "data" / "bot.lock"


def main() -> int:
    lock_path = _lock_path()
    if lock_path.exists():
        lock_path.unlink()
        print(f"Removed lock file: {lock_path}")
    else:
        print("No lock file found")

    db_path = _production_db_path()
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 0

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM kv_store;")
        conn.execute(
            """
            UPDATE signals
            SET status = 'CANCELLED'
            WHERE status IN ('PENDING', 'ACTIVE', 'PARTIAL_TP1');
            """
        )
        conn.commit()

    print("State reset completed: kv_store cleared, active signals cancelled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
