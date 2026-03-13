from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run UAT dry-run pulse against isolated state")
    parser.add_argument(
        "--force-signal",
        action="store_true",
        help="Inject a forced actionable signal after ingestion for threaded Telegram validation",
    )
    return parser


def _get_pulse_orchestrator_class():
    from src.core.orchestrator import PulseOrchestrator

    return PulseOrchestrator


def main(argv: list[str] | None = None) -> int:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Ensure this process always executes against isolated UAT resources.
    os.environ["UAT_MODE"] = "True"

    parser = _build_parser()
    args = parser.parse_args(argv)

    orchestrator_class = _get_pulse_orchestrator_class()
    orchestrator = orchestrator_class()
    orchestrator.run(force_signal=bool(args.force_signal))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
