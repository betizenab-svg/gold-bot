import os
import subprocess
import time
from pathlib import Path

from config.database import get_connection
from src.persistence.schema import SchemaInitializer


def _run_bot(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [env["PYTHON_EXE"], str(env["BOT_RUNNER"])],
        cwd=str(env["WORKSPACE"]),
        env=env,
        capture_output=True,
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


def main() -> int:
    workspace = Path(__file__).resolve().parent
    python_exe = workspace / ".venv" / "Scripts" / "python.exe"
    bot_runner = workspace / "src" / "bot_runner.py"
    db_path = workspace / "tests" / "test_trading_engine_short.db"
    lock_path = workspace / "data" / "bot.lock"
    log_path = workspace / "logs" / "daily-run.log"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        target = db_path.with_name(db_path.name + suffix) if suffix else db_path
        if target.exists():
            target.unlink()
    if lock_path.exists():
        lock_path.unlink()

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
            "MOCK_DELAY_SECONDS": "0",
        }
    )

    # Iteration 1: Normal run
    result = _run_bot(env)
    if result.returncode != 0:
        raise AssertionError("Iteration 1 failed")

    # Iteration 2: Thundering Herd
    first = _popen_bot(env)
    time.sleep(0.05)
    second = _popen_bot(env)
    out2, err2 = second.communicate(timeout=10)
    if second.returncode != 0:
        raise AssertionError("Iteration 2 second instance failed")
    out1, err1 = first.communicate(timeout=10)
    if first.returncode != 0:
        raise AssertionError("Iteration 2 first instance failed")

    # Iteration 3: Slow run to observe WAL file during execution
    env["MOCK_DELAY_SECONDS"] = "2"
    slow = _popen_bot(env)
    time.sleep(0.2)
    wal_path = db_path.with_name(db_path.name + "-wal")
    conn_wal = get_connection()
    SchemaInitializer(conn_wal).initialize()
    conn_wal.execute(
        """
        INSERT INTO kv_store (key, value, updated_at)
        VALUES ('wal_probe', '1', strftime('%s','now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at;
        """
    )
    conn_wal.commit()

    time.sleep(0.2)
    if not wal_path.exists():
        conn_wal.close()
        slow.terminate()
        raise AssertionError("WAL file not found during run; WAL mode may not be active")
    conn_wal.close()
    out_slow, err_slow = slow.communicate(timeout=10)
    if slow.returncode != 0:
        raise AssertionError("Iteration 3 failed")
    env["MOCK_DELAY_SECONDS"] = "0"

    # Iteration 4: Normal run
    result = _run_bot(env)
    if result.returncode != 0:
        raise AssertionError("Iteration 4 failed")

    # Iteration 5: Normal run
    result = _run_bot(env)
    if result.returncode != 0:
        raise AssertionError("Iteration 5 failed")

    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8")
        if "Lock acquisition failed" not in log_text:
            raise AssertionError("Expected lock acquisition failure log not found")
    else:
        raise AssertionError("Log file not found")

    conn = get_connection()
    try:
        SchemaInitializer(conn).initialize()
        count = conn.execute("SELECT COUNT(*) FROM market_data;").fetchone()[0]
        if count == 0:
            raise AssertionError("No market_data rows found")
    finally:
        conn.close()

    if lock_path.exists():
        raise AssertionError("Lock file remains after test")

    print("Sprint 8 Reliability & Soak Test Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
