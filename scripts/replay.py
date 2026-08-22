"""Full-fidelity replay: run the REAL pulse pipeline over historical candles.

Unlike the simplified backtester, this drives PulseOrchestrator itself -- all
strategies, confluence engines, governor, lifecycle, expiry, breakeven -- one
candle per pulse, exactly like production. Output: the same calibration
report the live bot produces, computed from what WOULD have happened.

Usage:
    .venv/bin/python scripts/replay.py --days 45
    .venv/bin/python scripts/replay.py --days 45 --symbol BTCUSD
    .venv/bin/python scripts/replay.py --csv path/to/history.csv
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Replay environment MUST be set before config.settings is imported anywhere.
_REPLAY_SYMBOL = "XAUUSD"
for _i, _arg in enumerate(sys.argv):
    if _arg == "--symbol" and _i + 1 < len(sys.argv):
        _REPLAY_SYMBOL = sys.argv[_i + 1].upper()
    elif _arg.startswith("--symbol="):
        _REPLAY_SYMBOL = _arg.split("=", 1)[1].upper()
_REPLAY_DB = os.path.join(tempfile.mkdtemp(prefix="gold_replay_"), "replay.db")
from config.instruments import get_instrument as _get_instrument  # noqa: E402

_REPLAY_TF = _get_instrument(_REPLAY_SYMBOL).signal_timeframe or "M5"
_TF_TO_YAHOO = {"M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h"}
os.environ["DB_PATH"] = _REPLAY_DB
os.environ["SIGNAL_TIMEFRAME"] = _REPLAY_TF
os.environ["SYMBOLS"] = _REPLAY_SYMBOL
os.environ["CHART_ALERTS_ENABLED"] = "0"
os.environ["NEWS_AUTOFETCH_ENABLED"] = "0"
os.environ["WEEKLY_REPORT_ENABLED"] = "0"
os.environ["DAILY_STATUS_ENABLED"] = "0"
os.environ["AUTO_QUARANTINE_ENABLED"] = "0"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["TELEGRAM_API_BASE_URL"] = "http://127.0.0.1:9"

from config.database import get_connection  # noqa: E402
from src.core.orchestrator import PulseOrchestrator  # noqa: E402
from src.domain.candle import Candle  # noqa: E402
from src.persistence.repository import Repository  # noqa: E402
from src.persistence.schema import SchemaInitializer  # noqa: E402


class ReplayClient:
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


def _download_history(days: int) -> list[Candle]:
    import datetime as dt

    import yfinance as yf

    from config.instruments import get_instrument

    instrument = get_instrument(_REPLAY_SYMBOL)
    ticker = instrument.yahoo_ticker
    interval = _TF_TO_YAHOO.get(_REPLAY_TF, "5m")
    print(
        f"Downloading {days} days of {_REPLAY_SYMBOL} ({ticker}) "
        f"{interval} history..."
    )
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=min(days, 59))

    frame = None
    for attempt in range(3):
        frame = yf.download(
            tickers=ticker,
            interval=interval,
            start=start,
            end=end,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if frame is not None and not frame.empty:
            break
        wait = 5 * (attempt + 1)
        print(f"  empty response (attempt {attempt + 1}/3), retrying in {wait}s...")
        time.sleep(wait)

    if (frame is None or frame.empty) and interval != "5m":
        # Yahoo sometimes rejects coarser intraday intervals; build the bars
        # ourselves from 5-minute data.
        print(f"  {interval} unavailable; downloading 5m and resampling locally...")
        frame = yf.download(
            tickers=ticker,
            interval="5m",
            start=start,
            end=end,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if frame is not None and not frame.empty:
            if hasattr(frame.columns, "get_level_values") and frame.columns.nlevels > 1:
                frame.columns = frame.columns.get_level_values(0)
            rule = {"15m": "15min", "30m": "30min", "1h": "1h"}.get(interval, "15min")
            frame = (
                frame.resample(rule, label="left", closed="left")
                .agg(
                    {
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum",
                    }
                )
                .dropna(subset=["Open", "High", "Low", "Close"])
            )
            # Drop the trailing bar: it may still be forming.
            frame = frame.iloc[:-1]
    if frame is None or frame.empty:
        raise SystemExit("Yahoo returned no data; try fewer --days or later.")

    if hasattr(frame.columns, "get_level_values") and frame.columns.nlevels > 1:
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])

    candles: list[Candle] = []
    for timestamp, row in frame.iterrows():
        candles.append(
            Candle(
                symbol=_REPLAY_SYMBOL,
                timeframe=_REPLAY_TF,
                timestamp=int(timestamp.timestamp()),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=0.0 if row.isna().get("Volume", False) else float(row["Volume"]),
            )
        )
    candles.sort(key=lambda c: c.timestamp)
    print(f"Loaded {len(candles)} candles: "
          f"{time.strftime('%Y-%m-%d', time.gmtime(candles[0].timestamp))} -> "
          f"{time.strftime('%Y-%m-%d', time.gmtime(candles[-1].timestamp))}")
    return candles


def _load_csv(path: str) -> list[Candle]:
    from src.backtest.csv_reader import CSVDataClient

    candles = CSVDataClient().load_data(path)
    for candle in candles:
        object.__setattr__(candle, "timeframe", _REPLAY_TF)
    print(f"Loaded {len(candles)} candles from {path}")
    return candles


def run_replay(candles: list[Candle], warmup: int = 600) -> dict:
    connection = get_connection()
    SchemaInitializer(connection).initialize()
    seed_repo = Repository(connection)

    # Preload warmup candles so engines have full history from pulse one,
    # and freeze the macro layer (it needs live internet, not history).
    seed_repo.save_candles(candles[:warmup])
    seed_repo.set_kv("last_macro_update_timestamp", str(int(time.time()) + 10 * 86400))
    seed_repo.set_kv("last_prune_timestamp", str(int(time.time()) + 10 * 86400))
    seed_repo.close()

    replay_client = ReplayClient(candles[warmup:])
    total_pulses = len(candles) - warmup

    def repository_factory() -> Repository:
        conn = get_connection()
        SchemaInitializer(conn).initialize()
        return Repository(conn)

    orchestrator = PulseOrchestrator(
        repository_factory=repository_factory,
        client_factory=lambda _repo: replay_client,
    )

    started = time.time()
    for pulse_index in range(total_pulses):
        orchestrator.run()
        if (pulse_index + 1) % 500 == 0:
            rate = (pulse_index + 1) / max(time.time() - started, 0.001)
            print(
                f"  pulse {pulse_index + 1}/{total_pulses} "
                f"({rate:.0f}/s, {(total_pulses - pulse_index - 1) / max(rate, 0.1):.0f}s left)"
            )

    return summarize(_REPLAY_DB)


def summarize(db_path: str) -> dict:
    from scripts.calibrate_from_history import OUTCOME_R, analyze

    report = analyze(db_path)

    connection = sqlite3.connect(db_path)
    rows = connection.execute(
        """
        SELECT status, COUNT(*) FROM signals GROUP BY status;
        """
    ).fetchall()
    connection.close()

    counts = {str(status): int(count) for status, count in rows}
    closed_r = 0.0
    for status, count in counts.items():
        closed_r += OUTCOME_R.get(status, 0.0) * count

    wins = counts.get("CLOSED_TP2", 0)
    losses = counts.get("CLOSED_SL", 0)
    breakeven = counts.get("CLOSED_BE", 0)
    resolved = wins + losses
    win_rate = (wins / resolved * 100.0) if resolved else 0.0

    summary = {
        "status_counts": counts,
        "net_r": round(closed_r, 2),
        "full_win_rate_pct": round(win_rate, 1),
        "wins_tp2": wins,
        "losses_sl": losses,
        "breakeven": breakeven,
        "strategies": report.get("strategies", {}),
        "recommendations": report.get("recommendations", []),
        "db_path": db_path,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the live pipeline over history")
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--symbol", type=str, default="XAUUSD")
    parser.add_argument("--csv", type=str, default=None)
    parser.add_argument("--out", type=str, default="data/replay_report.json")
    args = parser.parse_args()

    candles = _load_csv(args.csv) if args.csv else _download_history(args.days)
    if len(candles) < 700:
        raise SystemExit(f"Need at least 700 candles for a meaningful replay, got {len(candles)}")

    summary = run_replay(candles)

    print("\n================ REPLAY RESULT (real pipeline, real data) ================")
    print(f"Signals by status: {summary['status_counts']}")
    print(f"Net result: {summary['net_r']:+.2f}R | "
          f"TP2 wins: {summary['wins_tp2']} | SL: {summary['losses_sl']} | "
          f"BE: {summary['breakeven']} | full-win rate: {summary['full_win_rate_pct']}%")
    for name, stats in summary["strategies"].items():
        print(f"  {name}: {stats}")
    print("Recommendations:")
    for line in summary["recommendations"]:
        print(f"  - {line}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path} (replay DB kept at {summary['db_path']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
