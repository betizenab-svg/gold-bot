import errno
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# pyre-ignore[21]
from dotenv import load_dotenv

_fcntl: Any = None
_msvcrt: Any = None

try:
    import fcntl as _fcntl_mod
    _fcntl = _fcntl_mod
except ImportError:
    pass

try:
    import msvcrt as _msvcrt_mod
    _msvcrt = _msvcrt_mod
except ImportError:
    pass

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

LOCK_FILE_PATH = str(ROOT_DIR / "data" / "bot.lock")
LOG_FILE_PATH = str(ROOT_DIR / "logs" / "daily-run.log")


def _resolve_log_level() -> int:
    raw = os.getenv("BOT_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def setup_logging() -> None:
    log_path = Path(LOG_FILE_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = _resolve_log_level()
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(asctime)sZ %(name)s %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    logging.getLogger(__name__).info("Logging initialized at level=%s", logging.getLevelName(level))


def log_event(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    logging.info(f"{message} | {timestamp}")


def write_log_line(message: str) -> None:
    log_path = Path(LOG_FILE_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[INFO] {message} | {timestamp}\n")


def run_pulse() -> None:
    from src.core.orchestrator import PulseOrchestrator

    orchestrator = PulseOrchestrator()
    orchestrator.run()

    sleep_seconds = os.getenv("BOT_RUNNER_SLEEP_SECONDS")
    if sleep_seconds:
        try:
            delay = float(sleep_seconds)
        except ValueError:
            delay = 0
        if delay > 0:
            time.sleep(delay)


def acquire_lock(lock_file: Any) -> bool:
    if _fcntl is not None:
        try:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise

    if _msvcrt is None:
        raise RuntimeError("No supported file lock mechanism available")

    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()
            os.fsync(lock_file.fileno())
        lock_file.seek(0)
        _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_NBLCK, 1)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN) or getattr(exc, "winerror", None) == 33:
            return False
        raise


def release_lock(lock_file: Any) -> None:
    if _fcntl is not None:
        _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:
        lock_file.seek(0)
        _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_UNLCK, 1)


def main() -> int:
    lock_path = Path(LOCK_FILE_PATH)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Use binary mode to ensure Windows file locking works reliably.
    lock_file = open(lock_path, "a+b")
    try:
        if not acquire_lock(lock_file):
            write_log_line("Lock acquisition failed")
            return 0

        setup_logging()

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()).encode("utf-8"))
        lock_file.flush()
        os.fsync(lock_file.fileno())

        log_event("Lock acquired")
        try:
            run_pulse()
        finally:
            log_event("Lock released")
            release_lock(lock_file)
    finally:
        lock_file.close()
        try:
            if lock_path.exists():
                lock_path.unlink()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
