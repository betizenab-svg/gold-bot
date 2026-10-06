"""Free multi-year price history for the proof engine.

Sources (no account, no card):
- HistData.com: 1-minute bars for gold, silver, FX, Nasdaq/S&P and oil.
  Times are New York standard time WITHOUT daylight saving (UTC-5 all year).
- Binance public files (data.binance.vision): crypto klines.

Each finished month is stored once as data/history/<SYMBOL>/<TF>/<YYYY-MM>.csv.gz,
so an interrupted download (power or internet cut) resumes where it stopped.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
HISTORY_DIR = ROOT_DIR / "data" / "history"

Row = tuple[int, float, float, float, float, float]

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) gold-bot history fetcher"
TIMEFRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
BINANCE_INTERVALS = {"M5": "5m", "M15": "15m", "H1": "1h", "H4": "4h"}
EST_OFFSET = timedelta(hours=5)


def month_path(symbol: str, timeframe: str, year: int, month: int) -> Path:
    return HISTORY_DIR / symbol.upper() / timeframe.upper() / f"{year:04d}-{month:02d}.csv.gz"


def save_month(symbol: str, timeframe: str, year: int, month: int, rows: Iterable[Row]) -> Path:
    path = month_path(symbol, timeframe, year, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with gzip.open(temporary, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(row)
    temporary.replace(path)
    return path


def load_month(symbol: str, timeframe: str, year: int, month: int) -> list[Row]:
    path = month_path(symbol, timeframe, year, month)
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in csv.reader(handle)
            if r
        ]


def iter_months(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    months = []
    year, month = start
    while (year, month) <= end:
        months.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def parse_month(label: str) -> tuple[int, int]:
    year, month = label.split("-")
    return int(year), int(month)


def resample(rows: Iterable[Row], seconds: int) -> list[Row]:
    """1-minute rows -> bars of `seconds`, stamped at the bar's start (UTC)."""
    bars: list[list[float]] = []
    current: Optional[int] = None
    for ts, o, h, low, c, v in rows:
        bucket = ts - (ts % seconds)
        if bucket != current:
            bars.append([bucket, o, h, low, c, v])
            current = bucket
            continue
        bar = bars[-1]
        bar[2] = max(bar[2], h)
        bar[3] = min(bar[3], low)
        bar[4] = c
        bar[5] += v
    return [(int(b[0]), b[1], b[2], b[3], b[4], b[5]) for b in bars]


def split_by_month(rows: Iterable[Row]) -> dict[tuple[int, int], list[Row]]:
    months: dict[tuple[int, int], list[Row]] = {}
    for row in rows:
        moment = datetime.fromtimestamp(row[0], tz=timezone.utc)
        months.setdefault((moment.year, moment.month), []).append(row)
    return months


class HistDataSource:
    PAGE = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{pair}/{period}"
    DOWNLOAD = "https://www.histdata.com/get.php"

    def __init__(self, session: Optional[requests.Session] = None, pause_seconds: float = 2.0) -> None:
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.pause_seconds = pause_seconds

    @staticmethod
    def parse_csv(text: str) -> list[Row]:
        rows: list[Row] = []
        for line in text.splitlines():
            parts = line.strip().split(";")
            if len(parts) < 6:
                continue
            try:
                local = datetime.strptime(parts[0], "%Y%m%d %H%M%S")
            except ValueError:
                continue
            utc = (local + EST_OFFSET).replace(tzinfo=timezone.utc)
            rows.append(
                (
                    int(utc.timestamp()),
                    float(parts[1]),
                    float(parts[2]),
                    float(parts[3]),
                    float(parts[4]),
                    float(parts[5] or 0),
                )
            )
        rows.sort(key=lambda r: r[0])
        return rows

    def fetch(self, pair: str, year: int, month: Optional[int] = None) -> list[Row]:
        """A whole past year, or one month of the current year."""
        period = f"{year}" if month is None else f"{year}/{month}"
        page_url = self.PAGE.format(pair=pair.lower(), period=period)
        page = self.session.get(page_url, timeout=60)
        page.raise_for_status()
        fields = dict(
            re.findall(r'<input type="hidden" name="([a-z]+)" id="[a-z]+" value="([^"]*)"', page.text)
        )
        if "tk" not in fields:
            raise RuntimeError(f"HistData page for {pair} {period} has no download token")
        time.sleep(self.pause_seconds)
        response = self.session.post(
            self.DOWNLOAD,
            data={key: fields[key] for key in ("tk", "date", "datemonth", "platform", "timeframe", "fxpair") if key in fields},
            headers={"Referer": page_url},
            timeout=900,
        )
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise RuntimeError(f"HistData archive for {pair} {period} has no CSV")
            text = archive.read(names[0]).decode("utf-8", errors="replace")
        time.sleep(self.pause_seconds)
        return self.parse_csv(text)


class BinanceSource:
    URL = "https://data.binance.vision/data/spot/monthly/klines/{sym}/{interval}/{sym}-{interval}-{year:04d}-{month:02d}.zip"

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    @staticmethod
    def parse_csv(text: str) -> list[Row]:
        rows: list[Row] = []
        for record in csv.reader(io.StringIO(text)):
            if not record or not record[0].isdigit():
                continue
            raw_ts = int(record[0])
            # Binance switched to microseconds for 2025+ files.
            seconds = raw_ts // 1_000_000 if raw_ts > 10**14 else raw_ts // 1000
            rows.append(
                (seconds, float(record[1]), float(record[2]), float(record[3]), float(record[4]), float(record[5]))
            )
        rows.sort(key=lambda r: r[0])
        return rows

    def fetch_month(self, symbol: str, timeframe: str, year: int, month: int) -> list[Row]:
        interval = BINANCE_INTERVALS[timeframe]
        response = self.session.get(
            self.URL.format(sym=symbol, interval=interval, year=year, month=month), timeout=300
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8", errors="replace")
        return self.parse_csv(text)


def derive_month(symbol: str, timeframe: str, year: int, month: int) -> bool:
    """Build a slower chart month from a faster one already stored (no download)."""
    target = TIMEFRAME_SECONDS[timeframe]
    for finer, seconds in sorted(TIMEFRAME_SECONDS.items(), key=lambda item: item[1]):
        if seconds >= target or target % seconds:
            continue
        rows = load_month(symbol, finer, year, month)
        if rows:
            save_month(symbol, timeframe, year, month, resample(rows, target))
            return True
    return False


def fetch_symbol(
    symbol: str,
    source: str,
    source_symbol: str,
    timeframe: str | list[str],
    months: list[tuple[int, int]],
    histdata: Optional[HistDataSource] = None,
    binance: Optional[BinanceSource] = None,
    now: Optional[datetime] = None,
) -> int:
    """Download every missing finished month (every chart timeframe asked
    for); returns how many month files were saved."""
    timeframes = sorted(
        {timeframe} if isinstance(timeframe, str) else set(timeframe),
        key=lambda tf: TIMEFRAME_SECONDS[tf],
    )
    moment = now or datetime.now(timezone.utc)
    finished = [m for m in months if m < (moment.year, moment.month)]
    saved = 0
    # Slower charts are built from faster months already on disk first.
    for tf in timeframes[1:]:
        for year, month in finished:
            if not month_path(symbol, tf, year, month).exists() and derive_month(symbol, tf, year, month):
                saved += 1
    missing = [
        m for m in finished if any(not month_path(symbol, tf, *m).exists() for tf in timeframes)
    ]
    if not missing:
        return saved

    def store(year: int, month: int, rows: list[Row]) -> int:
        count = 0
        for tf in timeframes:
            if not month_path(symbol, tf, year, month).exists():
                save_month(symbol, tf, year, month, resample(rows, TIMEFRAME_SECONDS[tf]))
                count += 1
        return count

    if source == "BINANCE":
        client = binance or BinanceSource()
        for year, month in missing:
            rows = client.fetch_month(source_symbol, timeframes[0], year, month)
            if rows:
                saved += store(year, month, rows)
        return saved

    if source != "HISTDATA":
        raise ValueError(f"No free history source for {symbol}")
    client = histdata or HistDataSource()
    by_year: dict[int, list[int]] = {}
    for year, month in missing:
        by_year.setdefault(year, []).append(month)
    for year, wanted in sorted(by_year.items()):
        if year < moment.year:
            pieces = [client.fetch(source_symbol, year)]
        else:
            pieces = [client.fetch(source_symbol, year, month) for month in wanted]
        minute_rows = sorted((row for piece in pieces for row in piece), key=lambda r: r[0])
        for (row_year, row_month), rows in split_by_month(minute_rows).items():
            if row_year == year and row_month in wanted:
                saved += store(row_year, row_month, rows)
        logging.info("%s %s: saved %d month file(s)", symbol, year, saved)
    return saved
