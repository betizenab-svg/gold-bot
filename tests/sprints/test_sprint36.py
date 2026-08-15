from __future__ import annotations

import os
import stat

import pytest

from config.settings import BASE_DIR, DB_PATH, LOG_FILE_PATH
from scripts.harden_env import enforce_permissions


@pytest.fixture
def project_root() -> str:
    return str(BASE_DIR)


def test_path_resolution() -> None:
    assert os.path.isabs(DB_PATH), "DB_PATH must be absolute"
    assert os.path.isabs(LOG_FILE_PATH), "LOG_FILE_PATH must be absolute"


def test_htaccess_generation(project_root: str) -> None:
    data_htaccess = os.path.join(project_root, "data", ".htaccess")
    config_htaccess = os.path.join(project_root, "config", ".htaccess")
    logs_htaccess = os.path.join(project_root, "logs", ".htaccess")

    assert os.path.exists(data_htaccess), "Missing data/.htaccess"
    assert os.path.exists(config_htaccess), "Missing config/.htaccess"
    assert os.path.exists(logs_htaccess), "Missing logs/.htaccess"

    with open(data_htaccess, "r", encoding="utf-8") as handle:
        content = handle.read()

    assert "Require all denied" in content, "Apache 2.4 deny rule not found"
    assert "Options -Indexes" in content, "Directory listing protection not found"


def test_permission_hardening(project_root: str) -> None:
    dummy_path = os.path.join(project_root, "data", "dummy.db")
    with open(dummy_path, "w", encoding="utf-8") as handle:
        handle.write("dummy")

    try:
        enforce_permissions(extra_sensitive_files=[dummy_path])
        mode = stat.S_IMODE(os.stat(dummy_path).st_mode)
        if os.name == "posix":
            assert oct(mode) == "0o600", f"Expected 0o600, got {oct(mode)}"
        else:
            # Windows ACLs do not map exactly to POSIX chmod bits.
            assert os.path.exists(dummy_path), "Dummy file missing after hardening run"
    finally:
        if os.path.exists(dummy_path):
            os.remove(dummy_path)


def main() -> int:
    project_root = str(BASE_DIR)

    test_path_resolution()
    test_htaccess_generation(project_root)
    test_permission_hardening(project_root)

    print("Sprint 36 Security Hardening Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
