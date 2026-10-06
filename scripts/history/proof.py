"""Proof engine: run the REAL live pipeline over years of stored history.

Every candle goes through the same code the live bot runs (all strategies,
filters, risk governor, trade management). Work is saved after each month,
so a power or internet cut only loses the month in progress.

Usage:
    python scripts/history/proof.py --symbol XAUUSD --from 2023-10 --to 2026-09
    python scripts/history/proof.py --symbol XAUUSD --from 2023-10 --to 2026-09 \
        --variant be1.0 --set BE_ARM_R=1.0
    python scripts/history/proof.py --symbol XAUUSD ... --cot data/proof/cot_gold.json \
        --variant cot_penalty --set MACRO_GATES_MODE=penalty
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

PROOF_DIR = ROOT_DIR / "data" / "proof"
WARMUP_CANDLES = 600
KEEP_CANDLE_DAYS = 60


def _arg(name: str, default: str = "") -> str:
    for index, value in enumerate(sys.argv):
        if value == name and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if value.startswith(name + "="):
            return value.split("=", 1)[1]
    return default


def _overrides() -> dict[str, str]:
    pairs: dict[str, str] = {}
    for index, value in enumerate(sys.argv):
        if value == "--set" and index + 1 < len(sys.argv):
            key, _, setting = sys.argv[index + 1].partition("=")
            pairs[key.strip()] = setting.strip()
    return pairs


# The environment must be ready before config.settings is imported anywhere.
_SYMBOL = _arg("--symbol", "XAUUSD").upper()
_VARIANT = _arg("--variant", "base")
_WORK_DIR = PROOF_DIR / _VARIANT / _SYMBOL
# The busy database lives in fast temporary storage; months are checkpointed to disk.
_WORK_DB = Path(tempfile.gettempdir()) / "gold_proof" / _VARIANT / _SYMBOL / "replay.db"
_WORK_DB.parent.mkdir(parents=True, exist_ok=True)
from config.instruments import get_instrument as _get_instrument  # noqa: E402

_TIMEFRAME = _get_instrument(_SYMBOL).signal_timeframe or "M5"
os.environ.update(
    {
        "DB_PATH": str(_WORK_DB),
        "SIGNAL_TIMEFRAME": _TIMEFRAME,
        "SYMBOLS": _SYMBOL,
        "CHART_ALERTS_ENABLED": "0",
        "NEWS_AUTOFETCH_ENABLED": "0",
        "WEEKLY_REPORT_ENABLED": "0",
        "DAILY_STATUS_ENABLED": "0",
        "AUTO_QUARANTINE_ENABLED": "0",
        "OPS_TELEMETRY_ENABLED": "0",
        "SPOT_FEED_ENABLED": "0",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "TELEGRAM_ADMIN_CHAT_ID": "",
        "TELEGRAM_API_BASE_URL": "http://127.0.0.1:9",
        "BOT_PAUSED": "0",
        "SQLITE_SYNCHRONOUS": "OFF",
        "EVIDENCE_ENABLED": "0",
        "MONTHLY_RISK_REVIEW_ENABLED": "0",
    }
)
os.environ.update(_overrides())
logging.basicConfig(level=logging.ERROR)

from config.database import get_connection  # noqa: E402
from scripts.history.sources import iter_months, load_month, parse_month  # noqa: E402
from src.core.logger import StructuredLogger  # noqa: E402
from src.core.orchestrator import PulseOrchestrator  # noqa: E402
from src.domain.candle import Candle  # noqa: E402
from src.persistence.repository import Repository  # noqa: E402
from src.persistence.schema import SchemaInitializer  # noqa: E402


class _MonthFeed:
    """Feeds one historical candle per pulse, like a live feed replayed."""

    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.cursor = 0

    def fetch_latest_candles(self, _symbol: str, _timeframe: str) -> list[Candle]:
        if self.cursor >= len(self.candles):
            return []
        candle = self.candles[self.cursor]
        self.cursor += 1
        return [candle]


def _candles(year: int, month: int) -> list[Candle]:
    return [
        Candle(_SYMBOL, _TIMEFRAME, ts, o, h, low, c, v)
        for ts, o, h, low, c, v in load_month(_SYMBOL, _TIMEFRAME, year, month)
    ]


def _load_cot(path: str) -> list[tuple[int, str]]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return sorted((int(item["from"]), str(item["state"])) for item in payload)


def _cot_state_at(schedule: list[tuple[int, str]], timestamp: int) -> str:
    state = "NEUTRAL"
    for start, value in schedule:
        if start > timestamp:
            break
        state = value
    return state


def _write_progress(done: list[str], started: float) -> None:
    payload = {"symbol": _SYMBOL, "variant": _VARIANT, "done": done, "seconds": round(time.time() - started)}
    temporary = _WORK_DIR / "progress.tmp"
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(_WORK_DIR / "progress.json")


def _checkpoint() -> None:
    source = sqlite3.connect(str(_WORK_DB))
    target = sqlite3.connect(str(_WORK_DIR / "checkpoint.tmp"))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    (_WORK_DIR / "checkpoint.tmp").replace(_WORK_DIR / "checkpoint.db")


def _fresh_database(first_month: tuple[int, int]) -> None:
    for path in (_WORK_DB, Path(str(_WORK_DB) + "-wal"), Path(str(_WORK_DB) + "-shm")):
        if path.exists():
            path.unlink()
    connection = get_connection()
    SchemaInitializer(connection).initialize()
    repository = Repository(connection)
    year, month = first_month
    previous = (year - 1, 12) if month == 1 else (year, month - 1)
    warmup = _candles(*previous)[-WARMUP_CANDLES:]
    repository.save_candles(warmup)
    if warmup:
        repository.set_kv(f"last_processed_{_SYMBOL}", str(warmup[-1].timestamp))
    # The macro layer needs live internet; history runs feed it explicitly.
    far_future = str(int(time.time()) + 10 * 365 * 86400)
    repository.set_kv("last_macro_update_timestamp", far_future)
    repository.set_kv("last_prune_timestamp", far_future)
    repository.set_kv("macro_long_bias_multiplier", "1.0")
    repository.set_kv("macro_consensus_state", "NEUTRAL")
    repository.close()


def run(months: list[tuple[int, int]], cot_path: str, fresh: bool) -> dict:
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
    progress_file = _WORK_DIR / "progress.json"
    done: list[str] = []
    if not fresh and progress_file.exists() and (_WORK_DIR / "checkpoint.db").exists():
        done = list(json.loads(progress_file.read_text(encoding="utf-8")).get("done", []))
        # A leftover write-ahead log belongs to the abandoned run; replaying it would corrupt the restore.
        for leftover in (Path(str(_WORK_DB) + "-wal"), Path(str(_WORK_DB) + "-shm")):
            if leftover.exists():
                leftover.unlink()
        shutil.copyfile(_WORK_DIR / "checkpoint.db", _WORK_DB)
        print(f"Resuming {_SYMBOL}/{_VARIANT} after {done[-1] if done else 'start'}")
    else:
        _fresh_database(months[0])

    schedule = _load_cot(cot_path)
    feed_holder: dict[str, _MonthFeed] = {}
    orchestrator = PulseOrchestrator(
        repository_factory=lambda: Repository(get_connection()),
        client_factory=lambda _repo: feed_holder["feed"],
        structured_logger=StructuredLogger(str(_WORK_DIR / "telemetry.jsonl")),
    )
    started = time.time()
    for year, month in months:
        label = f"{year:04d}-{month:02d}"
        if label in done:
            continue
        candles = _candles(year, month)
        if not candles:
            print(f"  {label}: no history stored; skipped")
            done.append(label)
            continue
        feed_holder["feed"] = _MonthFeed(candles)
        kv_connection = sqlite3.connect(str(_WORK_DB))
        current_state = None
        month_started = time.time()
        for candle in candles:
            if schedule:
                state = _cot_state_at(schedule, candle.timestamp)
                if state != current_state:
                    with kv_connection:
                        kv_connection.execute(
                            "INSERT INTO kv_store (key, value, updated_at) VALUES ('macro_cot_state', ?, 0) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
                            (state,),
                        )
                    current_state = state
            orchestrator.run()
        newest = candles[-1].timestamp
        with kv_connection:
            kv_connection.execute(
                "DELETE FROM market_data WHERE timestamp < ?;", (newest - KEEP_CANDLE_DAYS * 86400,)
            )
        kv_connection.close()
        _checkpoint()
        done.append(label)
        _write_progress(done, started)
        rate = len(candles) / max(time.time() - month_started, 0.001)
        print(f"  {_SYMBOL}/{_VARIANT} {label}: {len(candles)} candles ({rate:.0f}/s)", flush=True)

    return export_results(months)


def export_results(months: list[tuple[int, int]]) -> dict:
    connection = sqlite3.connect(str(_WORK_DB))
    connection.row_factory = sqlite3.Row
    trades = [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, symbol, strategy, COALESCE(signal_type, type) AS direction, score,
                   order_type, COALESCE(entry_price, entry) AS entry, COALESCE(sl_price, sl) AS sl,
                   COALESCE(tp1_price, tp1) AS tp1, COALESCE(tp2_price, tp2) AS tp2,
                   timestamp, status, realized_r, closed_at, mfe_r, mae_r, closure_reason
            FROM signals ORDER BY timestamp, id;
            """
        )
    ]
    ideas = [
        dict(row)
        for row in connection.execute(
            """
            SELECT symbol, strategy, direction, order_type, score, classification, vetoes,
                   timestamp, entry_price AS entry, sl_price AS sl, shadow_status, shadow_r
            FROM setup_log WHERE shadow_status IS NOT NULL ORDER BY id;
            """
        )
    ]
    connection.close()
    from config import settings as app_settings

    payload = {
        "symbol": _SYMBOL,
        "variant": _VARIANT,
        "timeframe": _TIMEFRAME,
        "settings": _overrides(),
        # True: the broker spread is already inside every price and result.
        "cushion": bool(app_settings.SPREAD_CUSHION_ENABLED),
        "window": ["%04d-%02d" % months[0], "%04d-%02d" % months[-1]] if months else [],
        "trades": trades,
        "ideas": ideas,
    }
    (_WORK_DIR / "trades.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--variant", default="base")
    parser.add_argument("--set", action="append", default=[], help="SETTING=value for this run")
    parser.add_argument("--cot", default="", help="weekly big-trader positioning file")
    parser.add_argument("--fresh", action="store_true", help="ignore saved progress")
    args = parser.parse_args()

    months = iter_months(parse_month(args.start), parse_month(args.end))
    payload = run(months, args.cot, args.fresh)
    closed = [t for t in payload["trades"] if str(t["status"]).startswith("CLOSED")]
    print(
        f"{_SYMBOL}/{_VARIANT}: {len(payload['trades'])} signals, {len(closed)} closed, "
        f"{len(payload['ideas'])} followed ideas -> {_WORK_DIR / 'trades.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
