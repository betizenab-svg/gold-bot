"""Automatic history check for strategy changes (runs in CI).

Repeats the fresh "check" run (latest months, same data) with the new code and
compares each market with the result stored in config/evidence.json. A drop of
more than max(3R, 25%) fails the check so a harmful change is caught before
it goes live.

Usage: python scripts/history/check.py [--symbols XAUUSD,EURUSD]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.history.report import closed_trades  # noqa: E402
from src.analysis.evidence import load_evidence  # noqa: E402

TOLERANCE_R = 3.0
TOLERANCE_SHARE = 0.25


def allowed_drop(baseline: float) -> float:
    return max(TOLERANCE_R, TOLERANCE_SHARE * abs(baseline))


def compare(baseline: dict[str, float], current: dict[str, float]) -> list[str]:
    problems = []
    for symbol, before in baseline.items():
        if symbol not in current:
            continue
        after = current[symbol]
        if before - after > allowed_drop(before):
            problems.append(f"{symbol}: {before:+.1f}R before, {after:+.1f}R with this change")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="")
    args = parser.parse_args()
    evidence = load_evidence()
    window = evidence.get("check_window") or {}
    baseline = window.get("net_r_after_costs") or {}
    if not baseline:
        print("No stored check baseline yet; nothing to compare.")
        return 0
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or sorted(baseline)
    current: dict[str, float] = {}
    for symbol in symbols:
        code = subprocess.call(
            [
                sys.executable, str(ROOT_DIR / "scripts" / "history" / "proof.py"),
                "--symbol", symbol, "--from", window["from"], "--to", window["to"],
                "--variant", "ci_check", "--fresh",
            ],
            cwd=str(ROOT_DIR),
        )
        if code != 0:
            print(f"{symbol}: history run failed (exit {code})")
            return code
        payload = json.loads(
            (ROOT_DIR / "data" / "proof" / "ci_check" / symbol / "trades.json").read_text(encoding="utf-8")
        )
        current[symbol] = round(sum(t["r"] for t in closed_trades(payload)), 2)
        print(f"{symbol}: {baseline.get(symbol, 0.0):+.1f}R stored, {current[symbol]:+.1f}R now")
    problems = compare(baseline, current)
    if problems:
        print("::error::This change makes history results clearly worse:\n" + "\n".join(problems))
        return 1
    print("History check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
