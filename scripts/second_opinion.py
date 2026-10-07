"""Train and test the second-opinion model (item 98).

    .venv/bin/python scripts/second_opinion.py [--db data/trading_engine.db]

Writes config/second_opinion.json. The live bot only uses it when the
GitHub variable SECOND_OPINION_ENABLED=1 AND the test below says it helps.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.second_opinion import DEFAULT_PATH, build_model, load_examples  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "trading_engine.db"))
    parser.add_argument("--out", default=str(DEFAULT_PATH))
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    model = build_model(load_examples(conn))
    conn.close()
    Path(args.out).write_text(json.dumps(model, indent=2, sort_keys=True) + "\n")
    if not model["ready"]:
        print(f"Second opinion not ready: {model['why']}.")
        return 0
    test = model["walk_forward"]
    print(f"Learned from {model['examples']} finished ideas.")
    print(f"On periods it never saw: all ideas averaged {test['avg_all']:+.3f}R, "
          f"the ones it would keep averaged {test['avg_kept']:+.3f}R.")
    print("Verdict:", "HELPS - you may set SECOND_OPINION_ENABLED=1" if model["helps"]
          else "does not help - leave SECOND_OPINION_ENABLED off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
