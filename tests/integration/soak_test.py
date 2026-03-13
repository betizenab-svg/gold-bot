import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

workspace = Path(__file__).resolve().parents[2]
if str(workspace) not in sys.path:
    sys.path.insert(0, str(workspace))
db_path = workspace / "tests" / "test_trading_engine.db"
os.environ["DB_PATH"] = str(db_path)

from config.database import get_connection
from src.persistence.schema import SchemaInitializer

_resource: Any = None

try:
    import resource as _resource_mod
    _resource = _resource_mod
except ImportError:
    pass


def _cleanup_files(db_path: Path, lock_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = db_path.with_name(db_path.name + suffix) if suffix else db_path
        if target.exists():
            target.unlink()
    if lock_path.exists():
        lock_path.unlink()


def _run_bot(env: dict, capture_output: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [env["PYTHON_EXE"], str(env["BOT_RUNNER"])],
        cwd=str(env["WORKSPACE"]),
        env=env,
        capture_output=capture_output,
        text=True,
    )


def _popen_bot(env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [env["PYTHON_EXE"], str(env["BOT_RUNNER"])],
        cwd=str(env["WORKSPACE"]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _assert_no_db_lock(output: str, error: str) -> None:
    if "database is locked" in (output + error).lower():
        raise AssertionError("SQLite database is locked error detected")


def _check_db_integrity(db_path: Path, symbol: str, timeframe: str, step_seconds: int) -> None:
    conn = get_connection()
    try:
        SchemaInitializer(conn).initialize()
        rows = conn.execute(
            "SELECT timestamp FROM market_data WHERE symbol = ? AND timeframe = ? ORDER BY timestamp ASC;",
            (symbol, timeframe),
        ).fetchall()
        timestamps = [row[0] for row in rows]
        if not timestamps:
            raise AssertionError("No candles inserted during soak test")

        if len(timestamps) != len(set(timestamps)):
            raise AssertionError("Duplicate primary keys detected in market_data")

        for prev, curr in zip(timestamps, timestamps[1:]):
            if curr - prev != step_seconds:
                raise AssertionError("Timestamp gap detected in market_data")

        watermark = conn.execute(
            "SELECT value FROM kv_store WHERE key = 'last_processed_timestamp';"
        ).fetchone()
        if not watermark:
            raise AssertionError("Missing last_processed_timestamp watermark")
        if int(watermark[0]) != timestamps[-1]:
            raise AssertionError("Watermark does not match last candle timestamp")
    finally:
        conn.close()


def _get_parent_rss_mb() -> Optional[float]:
    if _resource is None:
        return None
    usage = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    if os.name == "posix":
        return usage / 1024
    return None


def main() -> int:
    python_exe = workspace / ".venv" / "Scripts" / "python.exe"
    bot_runner = workspace / "src" / "bot_runner.py"
    lock_path = workspace / "data" / "bot.lock"
    log_path = workspace / "logs" / "daily-run.log"

    _cleanup_files(db_path, lock_path)

    env = os.environ.copy()
    env.update(
        {
            "PYTHON_EXE": str(python_exe),
            "BOT_RUNNER": str(bot_runner),
            "WORKSPACE": str(workspace),
            "DB_PATH": str(db_path),
            "MOCK_INGESTION": "1",
            "MOCK_SYMBOL": "XAUUSD",
            "MOCK_TIMEFRAME": "M1",
            "MOCK_CANDLES_PER_RUN": "1",
            "MOCK_TIMEFRAME_SECONDS": "60",
        }
    )

    iterations = 50
    herd_attempts = 0
    random.seed(42)

    start_rss = _get_parent_rss_mb()

    for i in range(iterations):
        slow_pulse = i % 10 == 0
        herd = random.random() < 0.3

        env["MOCK_DELAY_SECONDS"] = "2" if slow_pulse else "0"

        first = _popen_bot(env)

        second = None
        if herd or slow_pulse:
            herd_attempts += 1
            time.sleep(0.1)
            second = _popen_bot(env)

        if second is not None:
            out2, err2 = second.communicate(timeout=10)
            _assert_no_db_lock(out2, err2)
            if second.returncode != 0:
                raise AssertionError("Second instance did not exit cleanly")

        out1, err1 = first.communicate(timeout=20)
        _assert_no_db_lock(out1, err1)
        if first.returncode != 0:
            raise AssertionError("Primary instance did not exit cleanly")

    if lock_path.exists():
        raise AssertionError("Lock file remains after soak test")

    _check_db_integrity(db_path, "XAUUSD", "M1", 60)

    end_rss = _get_parent_rss_mb()
    if start_rss is not None and end_rss is not None:
        if end_rss - start_rss > 20:
            raise AssertionError("Parent RSS increased unexpectedly")

    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8")
        lock_failures = log_text.count("Lock acquisition failed")
        if lock_failures < herd_attempts:
            raise AssertionError("Missing lock failure logs for thundering herd")

    print("Sprint 8 Soak Test Completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
