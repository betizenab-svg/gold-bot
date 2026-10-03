from __future__ import annotations

import sqlite3
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pandas as pd
import pytest

import scripts.backup_db as backup_db
import scripts.health_check as health_check
import scripts.reset_state as reset_state


def _paths(data_dir: Path) -> tuple[Path, Path, Path]:
    db_path = data_dir / "trading_engine.db"
    backups_dir = data_dir / "backups"
    return data_dir, db_path, backups_dir


@contextmanager
def _scripts_pointed_at(data_dir: Path) -> Iterator[None]:
    # The DR scripts hard-code the production DB; never let tests touch it.
    _, db_path, backups_dir = _paths(data_dir)
    with ExitStack() as stack:
        stack.enter_context(patch.object(backup_db, "_production_db_path", lambda: db_path))
        stack.enter_context(patch.object(backup_db, "_backup_dir", lambda: backups_dir))
        stack.enter_context(patch.object(reset_state, "_production_db_path", lambda: db_path))
        stack.enter_context(
            patch.object(reset_state, "_lock_path", lambda: data_dir / "bot.lock")
        )
        stack.enter_context(patch.object(health_check, "_production_db_path", lambda: db_path))
        yield


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
def data_dir(tmp_path: Path) -> Iterator[Path]:
    with _scripts_pointed_at(tmp_path):
        yield tmp_path


@pytest.fixture
def backups_dir(data_dir: Path) -> Path:
    return data_dir / "backups"


@pytest.fixture
def db_path(data_dir: Path) -> Path:
    target = data_dir / "trading_engine.db"
    _seed_dummy_db(target)
    return target


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


def test_health_check_mocked(db_path: Path) -> None:
    class _DummyResponse:
        status_code = 200

    with patch("scripts.health_check.yf.download", return_value=pd.DataFrame({"Close": [1.0]})), patch(
        "scripts.health_check.requests.get",
        return_value=_DummyResponse(),
    ):
        rc = health_check.main()

    assert rc == 0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sprint39_") as raw_dir:
        data_dir, db_path, backups_dir = _paths(Path(raw_dir))
        with _scripts_pointed_at(data_dir):
            _seed_dummy_db(db_path)
            test_backup_script(db_path, backups_dir)
            test_reset_state_script(db_path, data_dir)
            test_health_check_mocked(db_path)
    print("Sprint 39 Disaster Recovery & Deployment Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
