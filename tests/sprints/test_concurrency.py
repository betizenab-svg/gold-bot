import os
import re
import subprocess
import sys
import time
from pathlib import Path


def assert_condition(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_log_lines(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    return log_path.read_text(encoding="utf-8").splitlines()


def main() -> int:
    root_dir = Path(__file__).resolve().parent
    bot_runner = root_dir / "src" / "bot_runner.py"
    log_path = root_dir / "logs" / "daily-run.log"

    env = os.environ.copy()
    env["BOT_RUNNER_SLEEP_SECONDS"] = "5"
    env["MOCK_INGESTION"] = "1"

    before_lines = read_log_lines(log_path)

    first = subprocess.Popen(
        [sys.executable, str(bot_runner)],
        cwd=str(root_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    second_start = time.time()
    second = subprocess.Popen(
        [sys.executable, str(bot_runner)],
        cwd=str(root_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    second_out, second_err = second.communicate(timeout=5)
    second_duration = time.time() - second_start

    assert_condition(second.returncode == 0, "Second instance should exit with code 0")
    assert_condition(second_out == "", "Second instance should have empty stdout")
    assert_condition(second_err == "", "Second instance should have empty stderr")
    assert_condition(second_duration < 1.5, "Second instance should exit immediately")

    first_out, first_err = first.communicate(timeout=10)
    assert_condition(first.returncode == 0, "First instance should exit with code 0")
    assert_condition(first_out == "", "First instance should have empty stdout")
    assert_condition(first_err == "", "First instance should have empty stderr")

    after_lines = read_log_lines(log_path)
    new_lines = after_lines[len(before_lines):]

    lock_acquired = [line for line in new_lines if re.search(r"Lock acquired", line)]
    assert_condition(
        len(lock_acquired) == 1,
        f"Expected 1 lock acquisition, got {len(lock_acquired)}",
    )

    print("Concurrency test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
