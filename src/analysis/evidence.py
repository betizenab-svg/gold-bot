"""Evidence from years of history tests, as used by the live bot.

config/evidence.json is written by the proof engine (scripts/history/report.py)
and committed. The live bot reads it to switch off strategy-and-market pairs
that lose after costs, to stay quiet in hours that lose, and to compare live
results with what history predicts. A missing file changes nothing.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from config.instruments import get_instrument

EVIDENCE_PATH = Path(__file__).resolve().parents[2] / "config" / "evidence.json"

_cache: dict[str, Any] = {"mtime": None, "data": {}}


def load_evidence(path: Optional[Path] = None) -> dict[str, Any]:
    # History runs measure the raw strategies, so they must not apply past evidence.
    if os.getenv("EVIDENCE_ENABLED", "1") == "0":
        return {}
    target = path or EVIDENCE_PATH
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return {}
    if path is None and _cache["mtime"] == mtime:
        return _cache["data"]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if path is None:
        _cache.update({"mtime": mtime, "data": data})
    return data


def disabled_pair(symbol: str, strategy: str, evidence: Optional[dict] = None) -> bool:
    data = evidence if evidence is not None else load_evidence()
    wanted = (str(symbol).upper(), str(strategy).upper())
    for item in data.get("disabled_pairs", []) or []:
        if (str(item.get("symbol", "")).upper(), str(item.get("strategy", "")).upper()) == wanted:
            return True
    return False


def quiet_hour(symbol: str, timestamp: int, evidence: Optional[dict] = None) -> bool:
    data = evidence if evidence is not None else load_evidence()
    hours = (data.get("quiet_hours", {}) or {}).get(str(symbol).upper(), [])
    hour = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).hour
    return hour in {int(h) for h in hours}


def baseline(symbol: str, strategy: str, evidence: Optional[dict] = None) -> Optional[dict]:
    data = evidence if evidence is not None else load_evidence()
    return (data.get("baselines", {}) or {}).get(f"{str(symbol).upper()}|{str(strategy).upper()}")


def cost_in_r(symbol: str, entry: Any, sl: Any, spread_included: bool = False) -> float:
    """Round-trip spread plus slippage on both fills, as a share of the risk.
    With the spread cushion the spread is already inside the prices."""
    try:
        risk = abs(float(entry) - float(sl))
    except (TypeError, ValueError):
        return 0.0
    if risk <= 0:
        return 0.0
    instrument = get_instrument(symbol)
    spread = 0.0 if spread_included else instrument.typical_spread
    return (spread + 2.0 * instrument.slippage) / risk


def summarize(r_values: Iterable[float]) -> dict[str, Any]:
    values = [float(v) for v in r_values]
    count = len(values)
    if count == 0:
        return {"trades": 0}
    wins = sum(1 for v in values if v > 0)
    gross_win = sum(v for v in values if v > 0)
    gross_loss = -sum(v for v in values if v < 0)
    mean = sum(values) / count
    variance = sum((v - mean) ** 2 for v in values) / (count - 1) if count > 1 else 0.0
    peak = total = worst = 0.0
    streak = longest = 0
    for value in values:
        total += value
        peak = max(peak, total)
        worst = min(worst, total - peak)
        streak = streak + 1 if value < 0 else 0
        longest = max(longest, streak)
    return {
        "trades": count,
        "win_rate_pct": round(100.0 * wins / count, 1),
        "net_r": round(sum(values), 2),
        "expectancy_r": round(mean, 3),
        "std_r": round(math.sqrt(variance), 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_r": round(abs(worst), 2),
        "longest_losing_streak": longest,
    }
