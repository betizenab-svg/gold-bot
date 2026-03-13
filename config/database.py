import os
import sqlite3

from config.settings import DB_PATH
from src.persistence.schema import build_local_sqlite_uri


def get_connection() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(build_local_sqlite_uri(DB_PATH), uri=True)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=3000;")
    return conn
