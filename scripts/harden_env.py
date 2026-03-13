from __future__ import annotations

import os
import stat
from typing import Iterable, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _abs_path(*parts: str) -> str:
    return os.path.abspath(os.path.join(PROJECT_ROOT, *parts))


def _safe_chmod(path: str, mode: int) -> None:
    if not os.path.exists(path):
        return
    try:
        os.chmod(path, mode)
    except PermissionError:
        # Keep deployment script non-fatal on restricted hosts.
        return


def enforce_permissions(extra_sensitive_files: Optional[Iterable[str]] = None) -> None:
    sensitive_files = [
        _abs_path(".env"),
        _abs_path("data", "trading_engine.db"),
    ]
    if extra_sensitive_files:
        sensitive_files.extend(os.path.abspath(path) for path in extra_sensitive_files)

    owner_exec_targets = [
        _abs_path("src", "bot_runner.py"),
        _abs_path("scripts"),
    ]

    htaccess_files = [
        _abs_path("data", ".htaccess"),
        _abs_path("config", ".htaccess"),
        _abs_path("logs", ".htaccess"),
    ]

    for path in sensitive_files:
        _safe_chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600

    for path in owner_exec_targets:
        _safe_chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)  # 0o700

    for path in htaccess_files:
        _safe_chmod(
            path,
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
        )  # 0o644


def main() -> int:
    enforce_permissions()
    print("Environment hardening complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
