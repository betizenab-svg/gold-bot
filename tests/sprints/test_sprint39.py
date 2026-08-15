from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import scripts.backup_db as backup_db
import scripts.health_check as health_check
import scripts.reset_state as reset_state
from config.settings import BASE_DIR


def _paths() -> tuple[Path, Path, Path]:
    data_dir = Path(BASE_DIR) / "data"
    db_path = data_dir / "trading_engine.db"
    backups_dir = data_dir / "backups"
    return data_dir, db_path, backups_dir


def _seed_dummy_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS dummy_data (id INTEGER PRIMARY KEY, name TEXT);")
        conn.execute("DELETE FROM dummy_data;")
        conn.execute("INSERT INTO dummy_data (name) VALUES ('row1');")

        conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER);")
        conn.execute("INSERT OR REPLACE INTO kv_store (key, value, updated_at) VALUES ('k','v',0);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_hash TEXT,
                status TEXT
            );
            """
        )
        conn.execute("DELETE FROM signals;")
        conn.execute("INSERT INTO signals (signal_hash, status) VALUES ('dummy-hash', 'ACTIVE');")
        conn.commit()


@pytest.fixture
def data_dir() -> Path:
    return Path(BASE_DIR) / "data"


@pytest.fixture
def backups_dir(data_dir: Path) -> Path:
    return data_dir / "backups"


@pytest.fixture
def db_path(data_dir: Path, backups_dir: Path):
    # These scripts operate on the production DB path, so tests seed a
    # disposable DB there and restore whatever existed before.
    target = data_dir / "trading_engine.db"
    backup_of_original = None
    if target.exists():
        backup_of_original = target.read_bytes()
    _seed_dummy_db(target)
    yield target
    try:
        if backup_of_original is not None:
            target.write_bytes(backup_of_original)
        elif target.exists():
            target.unlink()
    except PermissionError:
        pass
    if backups_dir.exists():
        for path in backups_dir.glob("trading_engine_*.db"):
            try:
                path.unlink()
            except PermissionError:
                pass


def test_backup_script(db_path: Path, backups_dir: Path) -> None:
    before = set(backups_dir.glob("trading_engine_*.db")) if backups_dir.exists() else set()

    rc = backup_db.main()
    assert rc == 0

    after = set(backups_dir.glob("trading_engine_*.db"))
    new_files = list(after - before)
    assert new_files, "No new backup file created"

    newest = sorted(new_files, key=lambda p: p.stat().st_mtime)[-1]
    with sqlite3.connect(str(newest)) as conn:
        row = conn.execute("SELECT name FROM dummy_data LIMIT 1;").fetchone()
    assert row is not None and row[0] == "row1"


def test_reset_state_script(db_path: Path, data_dir: Path) -> None:
    lock_path = data_dir / "bot.lock"
    lock_path.write_text("locked", encoding="utf-8")

    rc = reset_state.main()
    assert rc == 0
    assert not lock_path.exists(), "bot.lock was not removed"

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT status FROM signals WHERE signal_hash='dummy-hash';").fetchone()
    assert row is not None and row[0] == "CANCELLED"


def test_health_check_mocked() -> None:
    class _DummyResponse:
        status_code = 200

    with patch("scripts.health_check.yf.download", return_value=pd.DataFrame({"Close": [1.0]})), patch(
        "scripts.health_check.requests.get",
        return_value=_DummyResponse(),
    ):
        rc = health_check.main()

    assert rc == 0


def main() -> int:
    data_dir, db_path, backups_dir = _paths()
    _seed_dummy_db(db_path)

    try:
        test_backup_script(db_path, backups_dir)
        test_reset_state_script(db_path, data_dir)
        test_health_check_mocked()
        print("Sprint 39 Disaster Recovery & Deployment Verified")
        return 0
    finally:
        if db_path.exists():
            try:
                db_path.unlink()
            except PermissionError:
                pass
        if backups_dir.exists():
            for path in backups_dir.glob("trading_engine_*.db"):
                try:
                    path.unlink()
                except PermissionError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
