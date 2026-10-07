"""One command: fetch the bot's latest saved database and open the control room.

    .venv/bin/python scripts/control_room.py            # download + open
    .venv/bin/python scripts/control_room.py --offline  # reuse last download

The live bot copies its database into the public repository at most every
6 hours, so what you see can be up to 6 hours old. Nothing here can change
the live bot: it only reads a private copy in data/control_room.db.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sqlite3
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_URL = (
    "https://raw.githubusercontent.com/betizenab-svg/gold-bot/main/data/trading_engine.db"
)
LOCAL_COPY = ROOT / "data" / "control_room.db"


def download(url: str, target: Path) -> None:
    partial = target.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    check = sqlite3.connect(partial)
    try:
        check.execute("PRAGMA schema_version;").fetchone()  # is it a database?
    finally:
        check.close()
    partial.replace(target)


def build_app(local_copy: Path):
    """The dashboard, reading the private copy (never the live database)."""
    from src.persistence.schema import SchemaInitializer

    conn = sqlite3.connect(local_copy)
    SchemaInitializer(conn).initialize()
    conn.commit()
    conn.close()
    # The package also exports a Flask object named "app"; load the module itself.
    dashboard = importlib.import_module("src.dashboard.app")
    dashboard.DB_PATH = str(local_copy)
    return dashboard.create_app()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("CONTROL_ROOM_DB_URL", DEFAULT_URL))
    parser.add_argument("--offline", action="store_true", help="use the last downloaded copy")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not args.offline:
        print("Downloading the bot's latest saved database ...")
        try:
            download(args.url, LOCAL_COPY)
        except Exception as error:  # network outages are normal here
            if not LOCAL_COPY.exists():
                print(f"Download failed and there is no earlier copy: {error}")
                return 1
            print(f"Download failed ({error}); showing the earlier copy instead.")
    elif not LOCAL_COPY.exists():
        print("No earlier copy yet - run once without --offline.")
        return 1

    # Point the app at the private copy BEFORE importing settings.
    os.environ["DB_PATH"] = str(LOCAL_COPY)
    from config import settings

    settings.DB_PATH = str(LOCAL_COPY)
    app = build_app(LOCAL_COPY)

    address = f"http://127.0.0.1:{args.port}/control"
    print(f"Control room: {address}  (log in with DASHBOARD_USERNAME / DASHBOARD_PASSWORD from .env)")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(address)).start()
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
