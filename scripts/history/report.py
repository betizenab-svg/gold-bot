"""Turn proof-engine runs into plain-English scorecards and config/evidence.json.

Usage:
    python scripts/history/report.py                   # report only
    python scripts/history/report.py --write-evidence  # also update config/evidence.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings as app_settings  # noqa: E402
from config.instruments import get_instrument  # noqa: E402
from src.analysis.evidence import EVIDENCE_PATH, cost_in_r, summarize  # noqa: E402
from src.analysis.luck_test import luck_test  # noqa: E402
from src.analysis.outcomes import derive_realized_r, row_r  # noqa: E402

PROOF_DIR = ROOT_DIR / "data" / "proof"

# Conservative switches: only clear, well-sampled losers are acted on.
MIN_PAIR_TRADES = 30
DISABLE_EXPECTANCY = -0.05
DISABLE_PROFIT_FACTOR = 0.95
MIN_HOUR_TRADES = 25
QUIET_HOUR_EXPECTANCY = -0.15
MARKET_PASS_TRADES = 50
MARKET_PASS_PROFIT_FACTOR = 1.1
TRAIN_MONTHS = 12
TEST_MONTHS = 3


def load_runs(proof_dir: Path) -> dict[tuple[str, str], dict]:
    runs: dict[tuple[str, str], dict] = {}
    for path in sorted(proof_dir.rglob("trades.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload.get("variant")) in {"smoke", "ci_check"}:
            continue
        runs[(str(payload["variant"]), str(payload["symbol"]))] = payload
    return runs


def _month(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m")


def closed_trades(payload: dict) -> list[dict]:
    output = []
    for trade in payload.get("trades", []):
        status = str(trade.get("status", ""))
        if not status.startswith("CLOSED"):
            continue
        raw = trade.get("realized_r")
        if raw is None:
            raw = derive_realized_r(
                status, trade.get("closure_reason"), trade.get("direction"),
                trade.get("entry"), trade.get("sl"), trade.get("tp2"),
            )
        r_raw = row_r(status, raw)
        if r_raw is None:
            continue
        symbol = str(trade.get("symbol") or payload["symbol"])
        moment = datetime.fromtimestamp(int(trade["timestamp"]), tz=timezone.utc)
        cost = cost_in_r(symbol, trade.get("entry"), trade.get("sl"), bool(payload.get("cushion")))
        output.append(
            {
                "symbol": symbol,
                "strategy": str(trade.get("strategy") or "UNKNOWN").upper(),
                "score": int(trade.get("score") or 0),
                "r_raw": float(r_raw),
                "r": float(r_raw) - cost,
                "hour": moment.hour,
                "weekday": moment.strftime("%a"),
                "month": moment.strftime("%Y-%m"),
                "timestamp": int(trade["timestamp"]),
            }
        )
    return output


def reason_category(classification: str, vetoes: str) -> str:
    text = str(vetoes or "")
    lowered = text.lower()
    if "penalty: big-picture" in lowered:
        return "Big-picture check (score lowered)"
    if classification == "BLOCKED":
        if "blocked:" in lowered:
            return "Big-picture check (hard block)"
        for marker, label in (
            ("dollar bet", "Same dollar bet already open"),
            ("correlated", "Same-currency bet already open"),
            ("already open", "Too many trades open"),
            ("daily signal cap", "Daily signal cap"),
            ("cooling down", "Cool-down after a stop"),
            ("halted", "Losing-streak pause"),
            ("daily loss", "Daily loss limit"),
            ("weekly loss", "Weekly loss brake"),
            ("profit locked", "Daily profit lock"),
            ("news", "News pause"),
            ("quiet hour", "Quiet hour"),
            ("one-way day", "One-way day block"),
            ("weekend", "Weekend close"),
            ("paused", "Owner pause"),
        ):
            if marker in lowered:
                return label
        return "Other block"
    if text.strip():
        first = text.split(";")[0].split(":")[0].strip()
        # "RSI 53" and "RSI 61" are the same filter.
        first = re.sub(r"\s+", " ", re.sub(r"\d+(?:\.\d+)?x?", "#", first)).strip()
        return f"Rejected: {first[:45]}"
    if classification == "WATCHLIST":
        return "Score just under the bar (watchlist)"
    return "Score too low"


def idea_outcomes(payload: dict) -> list[dict]:
    output = []
    for idea in payload.get("ideas", []):
        status = str(idea.get("shadow_status") or "")
        if not status.startswith("CLOSED") or idea.get("shadow_r") is None:
            continue
        symbol = str(idea.get("symbol") or payload["symbol"])
        output.append(
            {
                "symbol": symbol,
                "category": reason_category(str(idea.get("classification")), str(idea.get("vetoes") or "")),
                "r": float(idea["shadow_r"]) - cost_in_r(
                    symbol, idea.get("entry"), idea.get("sl"), bool(payload.get("cushion"))
                ),
            }
        )
    return output


def group(trades: Iterable[dict], key: Callable[[dict], Any]) -> dict[Any, dict]:
    buckets: dict[Any, list[float]] = defaultdict(list)
    for trade in trades:
        buckets[key(trade)].append(trade["r"])
    return {name: summarize(values) for name, values in buckets.items()}


def score_bucket(score: int) -> str:
    if score >= 90:
        return "90+"
    if score >= 85:
        return "85-89"
    if score >= 80:
        return "80-84"
    return "75-79"


def walk_forward(monthly: dict[str, dict[str, float]], months: list[str]) -> dict[str, Any]:
    """monthly[variant][YYYY-MM] = net R after costs. Pick the best variant on
    each 12-month training window and record how it did on the next 3 unseen months."""
    windows = []
    start = 0
    while start + TRAIN_MONTHS + TEST_MONTHS <= len(months):
        train = months[start:start + TRAIN_MONTHS]
        test = months[start + TRAIN_MONTHS:start + TRAIN_MONTHS + TEST_MONTHS]
        best = max(monthly, key=lambda v: sum(monthly[v].get(m, 0.0) for m in train))
        windows.append(
            {
                "train": f"{train[0]}..{train[-1]}",
                "test": f"{test[0]}..{test[-1]}",
                "chosen": best,
                "unseen_r": round(sum(monthly[best].get(m, 0.0) for m in test), 2),
            }
        )
        start += TEST_MONTHS
    picks: dict[str, int] = defaultdict(int)
    for window in windows:
        picks[window["chosen"]] += 1
    return {
        "windows": windows,
        "unseen_total_r": round(sum(w["unseen_r"] for w in windows), 2),
        "picks": dict(picks),
    }


def build(runs: dict[tuple[str, str], dict]) -> tuple[dict, dict, str]:
    base = {symbol: payload for (variant, symbol), payload in runs.items() if variant == "base"}
    report: dict[str, Any] = {"markets": {}, "pairs": {}, "hours": {}, "weekdays": {}, "scores": {}, "filters": {}}
    evidence: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "disabled_pairs": [],
        "quiet_hours": {},
        "baselines": {},
        "luck": {},
        "markets": {},
        "suggested_settings": {},
    }
    all_trades: list[dict] = []
    all_months: set[str] = set()
    for symbol, payload in sorted(base.items()):
        trades = closed_trades(payload)
        all_trades.extend(trades)
        all_months.update(t["month"] for t in trades)
        market = summarize(t["r"] for t in trades)
        market["net_r_before_costs"] = round(sum(t["r_raw"] for t in trades), 2)
        report["markets"][symbol] = market
        passed = (
            market.get("trades", 0) >= MARKET_PASS_TRADES
            and market.get("net_r", 0) > 0
            and (market.get("profit_factor") or 0) >= MARKET_PASS_PROFIT_FACTOR
        )
        evidence["markets"][symbol] = {
            "trades": market.get("trades", 0),
            "net_r_after_costs": market.get("net_r", 0.0),
            "profit_factor": market.get("profit_factor"),
            "passed": passed,
        }
        evidence["luck"][symbol] = luck_test([t["r"] for t in trades])

        for strategy, stats in group(trades, lambda t: t["strategy"]).items():
            report["pairs"][f"{symbol}|{strategy}"] = stats
            evidence["baselines"][f"{symbol}|{strategy}"] = {
                k: stats.get(k) for k in ("trades", "expectancy_r", "std_r")
            }
            if (
                stats["trades"] >= MIN_PAIR_TRADES
                and stats["expectancy_r"] <= DISABLE_EXPECTANCY
                and (stats.get("profit_factor") or 0) < DISABLE_PROFIT_FACTOR
            ):
                evidence["disabled_pairs"].append(
                    {
                        "symbol": symbol,
                        "strategy": strategy,
                        "trades": stats["trades"],
                        "expectancy_r": stats["expectancy_r"],
                    }
                )

        hours = group(trades, lambda t: t["hour"])
        report["hours"][symbol] = {str(h): s for h, s in sorted(hours.items())}
        quiet = sorted(
            int(h) for h, s in hours.items()
            if s["trades"] >= MIN_HOUR_TRADES and s["expectancy_r"] <= QUIET_HOUR_EXPECTANCY
        )
        if quiet:
            evidence["quiet_hours"][symbol] = quiet
        report["weekdays"][symbol] = group(trades, lambda t: t["weekday"])
        report["scores"][symbol] = group(trades, lambda t: score_bucket(t["score"]))
        report["filters"][symbol] = group(idea_outcomes(payload), lambda t: t["category"])

    evidence["luck"]["ALL"] = luck_test([t["r"] for t in sorted(all_trades, key=lambda t: t["timestamp"])])
    report["scores"]["ALL"] = group(all_trades, lambda t: score_bucket(t["score"]))
    months = sorted(all_months)
    if months:
        evidence["window"] = {"from": months[0], "to": months[-1]}
    # Baseline for the automatic check on every strategy change: a FRESH run of
    # the latest months (the check repeats exactly that run with the new code).
    check_runs = {symbol: payload for (variant, symbol), payload in runs.items() if variant == "check"}
    if check_runs:
        first_window = next(iter(check_runs.values())).get("window") or []
        if len(first_window) == 2:
            evidence["check_window"] = {
                "from": first_window[0],
                "to": first_window[1],
                "net_r_after_costs": {
                    symbol: round(sum(t["r"] for t in closed_trades(payload)), 2)
                    for symbol, payload in sorted(check_runs.items())
                },
                "trades": {
                    symbol: len(closed_trades(payload)) for symbol, payload in sorted(check_runs.items())
                },
            }

    # Parameter variants (e.g. be0.75 / be1.0 / be1.25): walk-forward per market.
    report["walk_forward"] = {}
    by_setting: dict[tuple[str, str], dict[str, dict[str, float]]] = defaultdict(dict)
    for (variant, symbol), payload in runs.items():
        settings = payload.get("settings") or {}
        if variant == "base" or len(settings) != 1:
            continue
        key = next(iter(settings))
        if key in {"MACRO_GATES_MODE"}:
            continue
        monthly: dict[str, float] = defaultdict(float)
        for trade in closed_trades(payload):
            monthly[trade["month"]] += trade["r"]
        by_setting[(key, symbol)][f"{key}={settings[key]}"] = dict(monthly)
    for (key, symbol), monthly_by_variant in by_setting.items():
        default = getattr(app_settings, key, None)
        label = f"{key}={default:g}" if isinstance(default, float) else f"{key}={default}"
        if default is not None and label not in monthly_by_variant and symbol in base:
            monthly: dict[str, float] = defaultdict(float)
            for trade in closed_trades(base[symbol]):
                monthly[trade["month"]] += trade["r"]
            monthly_by_variant[label] = dict(monthly)
    for (key, symbol), monthly in sorted(by_setting.items()):
        if len(monthly) < 2:
            continue
        result = walk_forward(monthly, months)
        report["walk_forward"][f"{symbol}|{key}"] = result
        if result["picks"]:
            favourite = max(result["picks"], key=lambda v: result["picks"][v])
            share = result["picks"][favourite] / max(1, len(result["windows"]))
            if share >= 0.6 and result["unseen_total_r"] > 0:
                evidence["suggested_settings"].setdefault(key, {})[symbol] = {
                    "value": favourite.split("=", 1)[1],
                    "why": (
                        f"chosen in {result['picks'][favourite]} of {len(result['windows'])} "
                        f"walk-forward windows; unseen months total {result['unseen_total_r']:+.1f}R"
                    ),
                }

    # Big-picture checks on gold: off vs penalty vs block.
    report["macro_modes"] = {}
    for (variant, symbol), payload in runs.items():
        mode = (payload.get("settings") or {}).get("MACRO_GATES_MODE")
        if mode:
            report["macro_modes"][f"{symbol}|{mode}"] = summarize(t["r"] for t in closed_trades(payload))
    for symbol in sorted({key.split("|")[0] for key in report["macro_modes"]}):
        options = {"off": report["markets"].get(symbol, {})}
        for key, stats in report["macro_modes"].items():
            if key.startswith(symbol + "|"):
                options[key.split("|")[1]] = stats
        ranked = sorted(
            ((mode, stats) for mode, stats in options.items() if stats.get("trades", 0) >= MIN_PAIR_TRADES),
            key=lambda item: item[1].get("net_r", 0.0),
            reverse=True,
        )
        if len(ranked) >= 2 and ranked[0][1]["net_r"] - ranked[1][1]["net_r"] >= 2.0:
            evidence["suggested_settings"].setdefault("MACRO_GATES_MODE", {})[symbol] = {
                "value": ranked[0][0],
                "why": f"best after costs: {ranked[0][1]['net_r']:+.1f}R vs {ranked[1][1]['net_r']:+.1f}R",
            }

    return report, evidence, render_markdown(report, evidence)


def _line(name: str, stats: dict) -> str:
    if not stats or not stats.get("trades"):
        return f"- {name}: no trades"
    pf = stats.get("profit_factor")
    return (
        f"- {name}: {stats['trades']} trades, {stats['net_r']:+.1f}R after costs "
        f"({stats['expectancy_r']:+.2f}R per trade, win rate {stats['win_rate_pct']:.0f}%, "
        f"profit factor {pf if pf is not None else 'n/a'}, worst dip {stats['max_drawdown_r']:.1f}R)"
    )


def render_markdown(report: dict, evidence: dict) -> str:
    window = evidence.get("window", {})
    lines = [
        "# History proof report",
        "",
        f"Tested period: {window.get('from', '?')} to {window.get('to', '?')} "
        f"(generated {evidence.get('generated_at')}). Every result below is after "
        "spread and slippage. R = one unit of risk.",
        "",
        "## Markets",
    ]
    for symbol, stats in report["markets"].items():
        verdict = "passes" if evidence["markets"].get(symbol, {}).get("passed") else "does not pass yet"
        lines.append(_line(f"{get_instrument(symbol).display_name} ({verdict})", stats))
    lines += ["", "## Switched off (clear losers after costs)"]
    if evidence["disabled_pairs"]:
        for item in evidence["disabled_pairs"]:
            lines.append(
                f"- {item['strategy']} on {item['symbol']}: {item['trades']} trades, "
                f"{item['expectancy_r']:+.2f}R per trade"
            )
    else:
        lines.append("- none")
    lines += ["", "## Quiet hours (UTC) where new signals pause"]
    if evidence["quiet_hours"]:
        for symbol, hours in evidence["quiet_hours"].items():
            lines.append(f"- {symbol}: {', '.join(f'{h:02d}:00' for h in hours)}")
    else:
        lines.append("- none")
    lines += ["", "## Do higher scores win more?"]
    for bucket, stats in sorted(report["scores"].get("ALL", {}).items()):
        lines.append(_line(f"score {bucket}", stats))
    lines += ["", "## What each filter saved or missed (followed ideas that were not sent)"]
    for symbol, categories in report["filters"].items():
        for name, stats in sorted(categories.items(), key=lambda item: item[1].get("net_r", 0.0)):
            effect = "saved money" if stats.get("net_r", 0) < 0 else "missed profit"
            lines.append(f"- {symbol} | {name}: {stats['trades']} ideas would have made {stats['net_r']:+.1f}R ({effect})")
    luck = evidence["luck"].get("ALL", {})
    if luck.get("trades"):
        lines += [
            "",
            "## Luck test (all markets together)",
            f"- Worst dip that actually happened: {luck['actual_max_drawdown_r']}R; "
            f"typical: {luck['typical_max_drawdown_r']}R; bad luck (1 in 20): {luck['bad_luck_max_drawdown_r']}R",
            f"- Longest losing run that happened: {luck['actual_losing_streak']}; "
            f"bad luck (1 in 20): {luck['bad_luck_losing_streak']}",
        ]
    if evidence["suggested_settings"]:
        lines += ["", "## Suggested settings (never applied automatically)"]
        for key, per_symbol in evidence["suggested_settings"].items():
            for symbol, item in per_symbol.items():
                lines.append(f"- {symbol}: {key} = {item['value']} ({item['why']})")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof-dir", default=str(PROOF_DIR))
    parser.add_argument("--write-evidence", action="store_true")
    args = parser.parse_args()
    proof_dir = Path(args.proof_dir)
    runs = load_runs(proof_dir)
    if not runs:
        print("No proof runs found.")
        return 1
    report, evidence, markdown = build(runs)
    proof_dir.mkdir(parents=True, exist_ok=True)
    (proof_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (proof_dir / "report.md").write_text(markdown, encoding="utf-8")
    if args.write_evidence:
        EVIDENCE_PATH.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
