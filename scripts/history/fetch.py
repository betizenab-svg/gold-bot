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

from config.instruments import INSTRUMENTS, history_key  # noqa: E402
from scripts.history.sources import fetch_symbol, iter_months, parse_month  # noqa: E402


def timeframes_for(key: str) -> list[str]:
    """Every chart timeframe used by the markets sharing this history."""
    return sorted(
        {
            instrument.signal_timeframe or "M5"
            for instrument in INSTRUMENTS.values()
            if history_key(instrument.symbol) == key
        }
    )


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
    keys: list[str] = []
    for symbol in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        key = history_key(symbol) if symbol in INSTRUMENTS else symbol
        if key not in keys:
            keys.append(key)
    for key in keys:
        instrument = INSTRUMENTS.get(key)
        if instrument is None or not instrument.history_source:
            logging.warning("%s has no free history source; skipped", key)
            continue
        for attempt in range(1, args.attempts + 1):
            try:
                saved = fetch_symbol(
                    key, instrument.history_source, instrument.history_symbol, timeframes_for(key), months
                )
                logging.info("%s: %d new month file(s) saved", key, saved)
                break
            except Exception as exc:
                logging.warning("%s attempt %d failed: %s", key, attempt, exc)
                time.sleep(30 * attempt)
        else:
            failed.append(key)
    if failed:
        logging.error("History download failed for: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
