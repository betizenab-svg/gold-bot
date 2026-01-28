import errno
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl  # type: ignore
except ImportError:
    fcntl = None

try:
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.settings import LOCK_FILE_PATH, LOG_FILE_PATH


def setup_logging() -> None:
    log_path = Path(LOG_FILE_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )


def log_event(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    logging.info(f"{message} | {timestamp}")


def run_pulse() -> None:
    # Placeholder for main signal logic
    logging.info("Pulse executed (placeholder)")
    sleep_seconds = os.getenv("BOT_RUNNER_SLEEP_SECONDS")
    if sleep_seconds:
        try:
            delay = float(sleep_seconds)
        except ValueError:
            delay = 0
        if delay > 0:
            time.sleep(delay)


def acquire_lock(lock_file) -> bool:
    if fcntl is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise

    if msvcrt is None:
        raise RuntimeError("No supported file lock mechanism available")

    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write("0")
            lock_file.flush()
            os.fsync(lock_file.fileno())
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN) or getattr(exc, "winerror", None) == 33:
            return False
        raise


def release_lock(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def main() -> int:
    setup_logging()
    lock_path = Path(LOCK_FILE_PATH)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        if not acquire_lock(lock_file):
            return 0

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
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
