"""Monthly results report from the public ledger: a PDF and share-ready images.

Usage:
    python scripts/monthly_report.py               # last month
    python scripts/monthly_report.py --month 2026-09 --post

Writes site/reports/<YYYY-MM>.png (portrait), -square.png, -story.png and
.pdf, and lists the month in site/reports/index.json. With --post the image
and the PDF are sent to the public Telegram channel.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.instruments import get_instrument  # noqa: E402
from src.alerting.public_posts import render_results_card  # noqa: E402
from src.alerting.timefmt import eat_datetime  # noqa: E402
from src.analysis.luck_test import longest_losing_streak  # noqa: E402

LEDGER = ROOT_DIR / "site" / "data" / "ledger.jsonl"
REPORTS = ROOT_DIR / "site" / "reports"
SIZES = {"": (1080, 1350), "-square": (1080, 1080), "-story": (1080, 1920)}


def previous_month(now: datetime) -> str:
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return f"{year:04d}-{month:02d}"


def month_trades(ledger_path: Path, month: str) -> list[dict[str, Any]]:
    trades = []
    if not ledger_path.exists():
        return trades
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("event") != "CLOSE" or item.get("result_r") is None:
            continue
        if str(item.get("event_time", "")).startswith(month):
            trades.append(item)
    trades.sort(key=lambda t: t["event_time"])
    return trades


def month_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(t["result_r"]) for t in trades]
    markets: dict[str, float] = {}
    for trade in trades:
        markets[trade["symbol"]] = markets.get(trade["symbol"], 0.0) + float(trade["result_r"])
    return {
        "trades": len(values),
        "wins": sum(1 for v in values if v > 0),
        "losses": sum(1 for v in values if v < 0),
        "flat": sum(1 for v in values if v == 0),
        "net_r": round(sum(values), 2),
        "markets": {k: round(v, 2) for k, v in sorted(markets.items())},
        "curve": [round(sum(values[: i + 1]), 2) for i in range(len(values))],
        "worst_streak": longest_losing_streak(values),
    }


def label(month: str) -> str:
    return datetime.strptime(month, "%Y-%m").strftime("%B %Y")


def caption(month: str, stats: dict[str, Any]) -> str:
    if not stats["trades"]:
        return f"\U0001f4c5 <b>Monthly report, {label(month)}</b>\nNo finished trades this month."
    lines = [
        f"\U0001f4c5 <b>Monthly report, {label(month)}</b>",
        f"<b>{stats['net_r']:+.2f}R</b> from {stats['trades']} trades: {stats['wins']} won, "
        f"{stats['losses']} lost, {stats['flat']} closed at entry. Worst losing streak: {stats['worst_streak']}.",
    ]
    for symbol, value in stats["markets"].items():
        lines.append(f"\u2022 {get_instrument(symbol).display_name}: {value:+.2f}R")
    lines.append("<i>Every trade counted, losses included. Full list in the PDF.</i>")
    return "\n".join(lines)


def build_pdf(month: str, stats: dict[str, Any], trades: list[dict[str, Any]], cover_png: bytes) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    buffer = io.BytesIO()
    with PdfPages(buffer) as pdf:
        cover = plt.figure(figsize=(8.27, 11.69))
        axis = cover.add_axes([0, 0, 1, 1])
        axis.imshow(mpimg.imread(io.BytesIO(cover_png), format="png"))
        axis.axis("off")
        pdf.savefig(cover)
        plt.close(cover)
        rows = [
            f"{t['code']:<8} {get_instrument(t['symbol']).display_name[:18]:<18} "
            f"{'Buy' if t['direction'] == 'LONG' else 'Sell':<5} {float(t['result_r']):+7.2f}R  "
            f"{eat_datetime(int(datetime.fromisoformat(t['event_time'].replace('Z', '+00:00')).timestamp()))}"
            for t in trades
        ] or ["No finished trades this month."]
        for start in range(0, len(rows), 45):
            page = plt.figure(figsize=(8.27, 11.69))
            page.text(0.07, 0.95, f"All finished trades, {label(month)} (times in EAT)", fontsize=13, weight="bold")
            page.text(0.07, 0.92, "Code     Market             Side   Result  Finished", family="monospace", fontsize=9)
            for index, row in enumerate(rows[start:start + 45]):
                page.text(0.07, 0.895 - index * 0.019, row, family="monospace", fontsize=9)
            page.text(0.07, 0.03, "1R = the amount risked on one trade. Past results do not promise future results.",
                      fontsize=8, color="#555555")
            pdf.savefig(page)
            plt.close(page)
    return buffer.getvalue()


def write_reports(month: str, ledger_path: Path = LEDGER, out_dir: Path = REPORTS) -> dict[str, Any]:
    trades = month_trades(ledger_path, month)
    stats = month_stats(trades)
    out_dir.mkdir(parents=True, exist_ok=True)
    images = {}
    for suffix, size in SIZES.items():
        png = render_results_card(f"Results \u00b7 {label(month)}", stats, size)
        (out_dir / f"{month}{suffix}.png").write_bytes(png)
        images[suffix] = png
    (out_dir / f"{month}.pdf").write_bytes(build_pdf(month, stats, trades, images[""]))
    index_path = out_dir / "index.json"
    months = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    if month not in months:
        months = sorted(months + [month])
    index_path.write_text(json.dumps(months) + "\n", encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="")
    parser.add_argument("--post", action="store_true", help="send the image and PDF to the public channel")
    args = parser.parse_args(argv)
    month = args.month or previous_month(datetime.now(timezone.utc))
    stats = write_reports(month)
    print(f"{month}: {stats['trades']} trades, {stats['net_r']:+.2f}R")
    if args.post:
        from src.alerting.telegram_client import TelegramClient

        client = TelegramClient()
        if client.bot_token and client.chat_id:
            client.send_photo((REPORTS / f"{month}.png").read_bytes(), caption=caption(month, stats))
            client.send_document((REPORTS / f"{month}.pdf").read_bytes(), f"report-{month}.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
