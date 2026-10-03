"""Sprint 55: the weekly replay workflow passes BE_ARM_R="" when no override is
given; settings must fall back to the default instead of crashing."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _settings_value(name: str, env_value: str) -> str:
    env = {**os.environ, name: env_value}
    result = subprocess.run(
        [sys.executable, "-c", f"import config.settings as s; print(s.{name})"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_empty_be_arm_r_uses_default() -> None:
    assert _settings_value("BE_ARM_R", "") == "0.75"


def test_be_arm_r_override_still_applies() -> None:
    assert _settings_value("BE_ARM_R", "1.25") == "1.25"
