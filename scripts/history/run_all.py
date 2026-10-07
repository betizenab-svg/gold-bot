"""Run the whole proof batch with a few parallel workers (resumable).

Usage:
    python scripts/history/run_all.py --from 2023-10 --to 2026-09 --workers 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.instruments import INSTRUMENTS, history_key  # noqa: E402
from scripts.history.sources import iter_months, parse_month  # noqa: E402

PYTHON = sys.executable
COT_FILE = ROOT_DIR / "data" / "proof" / "cot_gold.json"
COT_ARG = "data/proof/cot_gold.json"
CHECK_MONTHS = 6
# One setting changed per run; the report compares each with the default on
# months the choice was not made on (walk-forward).
SETTING_VARIANTS: list[tuple[str, list[str]]] = [
    ("be1.0", ["--set", "BE_ARM_R=1.0"]),  # when the stop moves to entry
    ("be1.25", ["--set", "BE_ARM_R=1.25"]),
    ("tp1_1.0", ["--set", "TP1_R=1.0"]),  # where to bank the first half
    ("tp2_2.0", ["--set", "TP2_R=2.0"]),  # where the second half is taken
    ("entry_market", ["--set", "ENTRY_MODE=market"]),  # enter now instead of waiting
    ("expiry_180", ["--set", "SIGNAL_EXPIRY_MINUTES=180"]),  # wait longer for the entry
    ("trendday_off", ["--set", "TREND_DAY_FILTER=off"]),  # does the one-way-day block help?
    ("mood_block", ["--set", "MOOD_FILTER=block"]),  # does skipping wild markets help?
]

Job = tuple[str, str, list[str], str, str]


def jobs(symbols: list[str], start: str, end: str) -> list[Job]:
    months = iter_months(parse_month(start), parse_month(end))
    check_start = "%04d-%02d" % months[-CHECK_MONTHS] if len(months) >= CHECK_MONTHS else start
    batch: list[Job] = []
    for symbol in symbols:
        batch.append((symbol, "base", [], start, end))
    for symbol in symbols:
        batch.append((symbol, "check", [], check_start, end))
    for symbol in symbols:
        if INSTRUMENTS.get(symbol) is not None and INSTRUMENTS[symbol].trial:
            continue  # trial markets first have to pass at all; settings come later
        for variant, extra in SETTING_VARIANTS:
            batch.append((symbol, variant, list(extra), start, end))
    if "XAUUSD" in symbols and COT_FILE.exists():
        for mode in ("penalty", "block"):
            batch.append(
                (
                    "XAUUSD",
                    f"cot_{mode}",
                    ["--set", f"MACRO_GATES_MODE={mode}", "--cot", COT_ARG],
                    start,
                    end,
                )
            )
    return batch


def run_job(job: Job, log_dir: Path) -> int:
    symbol, variant, extra, start, end = job
    log_path = log_dir / f"{variant}_{symbol}.log"
    command = [
        PYTHON, str(ROOT_DIR / "scripts" / "history" / "proof.py"),
        "--symbol", symbol, "--from", start, "--to", end, "--variant", variant, *extra,
    ]
    started = time.time()
    with log_path.open("a", encoding="utf-8") as log:
        code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT_DIR))
    print(f"{variant}/{symbol}: exit {code} after {time.time() - started:.0f}s", flush=True)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="XAUUSD,EURUSD,GBPUSD,BTCUSD")
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--only", default="", help="comma-separated variants to run")
    parser.add_argument("--matrix", action="store_true", help="print the jobs as a GitHub matrix and exit")
    args = parser.parse_args()
    batch = jobs([s.strip().upper() for s in args.symbols.split(",") if s.strip()], args.start, args.end)
    if args.only:
        wanted = {v.strip() for v in args.only.split(",")}
        batch = [job for job in batch if job[1] in wanted]
    if args.matrix:
        print(json.dumps({"include": [
            {"symbol": s, "key": history_key(s), "variant": v, "args": " ".join(extra), "from": a, "to": b}
            for s, v, extra, a, b in batch
        ]}))
        return 0
    log_dir = ROOT_DIR / "data" / "proof" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        codes = list(pool.map(lambda job: run_job(job, log_dir), batch))
    return 0 if all(code == 0 for code in codes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
