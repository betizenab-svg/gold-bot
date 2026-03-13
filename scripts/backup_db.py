from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from config.settings import BASE_DIR


def _production_db_path() -> Path:
    return Path(BASE_DIR) / "data" / "trading_engine.db"


def _backup_dir() -> Path:
    return Path(BASE_DIR) / "data" / "backups"


def _rotate_backups(folder: Path, keep_days: int = 7) -> int:
    cutoff = time.time() - (keep_days * 24 * 60 * 60)
    deleted = 0
    for path in folder.glob("trading_engine_*.db"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except FileNotFoundError:
            continue
    return deleted


def main() -> int:
    source_db = _production_db_path()
    backup_folder = _backup_dir()
    backup_folder.mkdir(parents=True, exist_ok=True)

    if not source_db.exists():
        raise FileNotFoundError(f"Source database does not exist: {source_db}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_folder / f"trading_engine_{stamp}.db"

    with sqlite3.connect(str(source_db)) as src, sqlite3.connect(str(backup_path)) as dst:
        src.backup(dst)

    with sqlite3.connect(str(backup_path)) as verify_conn:
        row = verify_conn.execute("PRAGMA integrity_check;").fetchone()
        if row is None or str(row[0]).lower() != "ok":
            raise RuntimeError(f"Backup integrity check failed: {row}")

    deleted_count = _rotate_backups(backup_folder, keep_days=7)
    print(f"Backup created: {backup_path}")
    print(f"Old backups deleted: {deleted_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
