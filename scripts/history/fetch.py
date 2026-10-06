"""Download free multi-year history for the proof engine (resumable).

Usage:
    python scripts/history/fetch.py --symbols XAUUSD,EURUSD,GBPUSD,BTCUSD --from 2023-10 --to 2026-09
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.instruments import INSTRUMENTS  # noqa: E402
from scripts.history.sources import fetch_symbol, iter_months, parse_month  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="XAUUSD,EURUSD,GBPUSD,BTCUSD")
    parser.add_argument("--from", dest="start", required=True, help="first month, YYYY-MM")
    parser.add_argument("--to", dest="end", required=True, help="last month, YYYY-MM")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    months = iter_months(parse_month(args.start), parse_month(args.end))
    failed: list[str] = []
    for symbol in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        instrument = INSTRUMENTS.get(symbol)
        if instrument is None or not instrument.history_source:
            logging.warning("%s has no free history source; skipped", symbol)
            continue
        timeframe = instrument.signal_timeframe or "M5"
        for attempt in range(1, args.attempts + 1):
            try:
                saved = fetch_symbol(
                    symbol, instrument.history_source, instrument.history_symbol, timeframe, months
                )
                logging.info("%s: %d new month(s) saved", symbol, saved)
                break
            except Exception as exc:
                logging.warning("%s attempt %d failed: %s", symbol, attempt, exc)
                time.sleep(30 * attempt)
        else:
            failed.append(symbol)
    if failed:
        logging.error("History download failed for: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
