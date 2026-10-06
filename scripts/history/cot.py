"""Weekly big-trader positioning (CFTC Commitments of Traders) for gold, as it
was known at each moment in the past, so history tests can include the same
COT check the live bot uses.

Usage:
    python scripts/history/cot.py --from 2022-06-01 --out data/proof/cot_gold.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.settings import COT_LOOKBACK_WEEKS  # noqa: E402
from src.analysis.cot_index import CotAnalyzer  # noqa: E402

URL = "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
GOLD_CODE = "088691"
# Positions are as of Tuesday and published Friday 15:30 New York time.
PUBLISH_DELAY = timedelta(days=3, hours=20, minutes=30)


def fetch_weekly_nets(start: str) -> list[tuple[datetime, float]]:
    response = requests.get(
        URL,
        params={
            "$select": "report_date_as_yyyy_mm_dd,m_money_positions_long_all,m_money_positions_short_all",
            "$where": f"cftc_contract_market_code='{GOLD_CODE}' AND report_date_as_yyyy_mm_dd >= '{start}'",
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": "5000",
        },
        timeout=60,
    )
    response.raise_for_status()
    rows = []
    for item in response.json():
        try:
            day = datetime.fromisoformat(str(item["report_date_as_yyyy_mm_dd"])[:10]).replace(tzinfo=timezone.utc)
            net = float(item["m_money_positions_long_all"]) - float(item["m_money_positions_short_all"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((day, net))
    return rows


def schedule(rows: list[tuple[datetime, float]]) -> list[dict]:
    analyzer = CotAnalyzer()
    output = []
    for index in range(len(rows)):
        window = [net for _, net in rows[max(0, index - COT_LOOKBACK_WEEKS + 1): index + 1]]
        if len(window) < COT_LOOKBACK_WEEKS:
            continue
        value = analyzer.calculate_index(window[-1], window)
        output.append(
            {
                "from": int((rows[index][0] + PUBLISH_DELAY).timestamp()),
                "report_date": rows[index][0].strftime("%Y-%m-%d"),
                "index": round(value, 1),
                "state": analyzer.evaluate_positioning(value),
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", default="2022-06-01")
    parser.add_argument("--out", default=str(ROOT_DIR / "data" / "proof" / "cot_gold.json"))
    args = parser.parse_args()
    weeks = schedule(fetch_weekly_nets(args.start))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(weeks, indent=0), encoding="utf-8")
    states: dict[str, int] = {}
    for week in weeks:
        states[week["state"]] = states.get(week["state"], 0) + 1
    print(f"{len(weeks)} weeks -> {out} {states}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
