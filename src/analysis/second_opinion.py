"""Second opinion: a small learning model that guesses if an idea will win.

It learns from finished ideas - real signals and the "what if" results of
stopped ideas - using only facts known when the idea appears (score, side,
hour, market, strategy). It is a plain logistic regression in numpy.

Safety rails:
- OFF by default (SECOND_OPINION_ENABLED=0).
- Trains only with at least MIN_EXAMPLES finished ideas.
- Walk-forward test: trained on the past, judged on the following period it
  never saw. The saved model is marked "helps" only if skipping its low-chance
  ideas would have raised the average result on those unseen periods.
- The live bot uses the model only when enabled AND marked "helps".
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

MIN_EXAMPLES = 500
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "second_opinion.json"


def load_examples(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    queries = (
        """SELECT symbol, strategy, direction, score, timestamp, shadow_r AS r
           FROM setup_log WHERE shadow_r IS NOT NULL""",
        """SELECT symbol, COALESCE(strategy,'UNKNOWN') AS strategy,
                  COALESCE(type, signal_type) AS direction, score, timestamp, realized_r AS r
           FROM signals WHERE status LIKE 'CLOSED%' AND realized_r IS NOT NULL""",
    )
    for query in queries:
        try:
            for row in conn.execute(query).fetchall():
                examples.append(dict(zip(("symbol", "strategy", "direction", "score", "timestamp", "r"), row)))
        except sqlite3.Error:
            continue
    examples.sort(key=lambda e: int(e["timestamp"] or 0))
    return examples


def _vocabulary(examples: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "symbol": sorted({str(e["symbol"]) for e in examples}),
        "strategy": sorted({str(e["strategy"]) for e in examples}),
    }


def features(example: dict[str, Any], vocab: dict[str, list[str]]) -> list[float]:
    hour = datetime.fromtimestamp(int(example.get("timestamp") or 0), tz=timezone.utc).hour
    angle = 2 * math.pi * hour / 24
    row = [
        1.0,
        float(example.get("score") or 0) / 100.0,
        1.0 if str(example.get("direction", "")).upper() in {"LONG", "BUY"} else 0.0,
        math.sin(angle),
        math.cos(angle),
    ]
    for key in ("symbol", "strategy"):
        value = str(example.get(key))
        row += [1.0 if value == item else 0.0 for item in vocab[key]]
    return row


def train(x: np.ndarray, y: np.ndarray, steps: int = 400, rate: float = 0.5, l2: float = 0.01) -> np.ndarray:
    weights = np.zeros(x.shape[1])
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-(x @ weights)))
        gradient = x.T @ (p - y) / len(y) + l2 * np.r_[0.0, weights[1:]]
        weights -= rate * gradient
    return weights


def probability(weights: Any, row: list[float]) -> float:
    z = float(np.dot(np.asarray(weights, dtype=float), np.asarray(row, dtype=float)))
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def walk_forward(examples: list[dict[str, Any]], folds: int = 4, threshold: float = 0.4) -> dict[str, Any]:
    """Train on everything before each fold, judge on the fold."""
    vocab = _vocabulary(examples)
    size = len(examples) // (folds + 1)
    kept_r: list[float] = []
    all_r: list[float] = []
    windows = []
    for fold in range(1, folds + 1):
        past, future = examples[: fold * size], examples[fold * size : (fold + 1) * size]
        if not past or not future:
            continue
        x = np.array([features(e, vocab) for e in past])
        y = np.array([1.0 if float(e["r"]) > 0 else 0.0 for e in past])
        weights = train(x, y)
        kept = [float(e["r"]) for e in future if probability(weights, features(e, vocab)) >= threshold]
        every = [float(e["r"]) for e in future]
        kept_r += kept
        all_r += every
        windows.append(
            {
                "unseen": len(every),
                "kept": len(kept),
                "avg_all": round(sum(every) / len(every), 3),
                "avg_kept": round(sum(kept) / len(kept), 3) if kept else None,
            }
        )
    avg_all = sum(all_r) / len(all_r) if all_r else 0.0
    avg_kept = sum(kept_r) / len(kept_r) if kept_r else 0.0
    better = sum(1 for w in windows if w["avg_kept"] is not None and w["avg_kept"] > w["avg_all"])
    helps = bool(kept_r) and avg_kept > avg_all and better >= max(1, math.ceil(0.75 * len(windows)))
    return {
        "windows": windows,
        "avg_all": round(avg_all, 3),
        "avg_kept": round(avg_kept, 3),
        "helps": helps,
    }


def build_model(examples: list[dict[str, Any]], threshold: float = 0.4) -> dict[str, Any]:
    if len(examples) < MIN_EXAMPLES:
        return {"ready": False, "examples": len(examples),
                "why": f"needs at least {MIN_EXAMPLES} finished ideas, has {len(examples)}"}
    test = walk_forward(examples, threshold=threshold)
    vocab = _vocabulary(examples)
    x = np.array([features(e, vocab) for e in examples])
    y = np.array([1.0 if float(e["r"]) > 0 else 0.0 for e in examples])
    weights = train(x, y)
    return {
        "ready": True,
        "examples": len(examples),
        "threshold": threshold,
        "vocab": vocab,
        "weights": [round(float(w), 6) for w in weights],
        "walk_forward": test,
        "helps": test["helps"],
    }


_cache: dict[str, Any] = {"path": None, "mtime": None, "model": None}


def load_model(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    target = Path(path or DEFAULT_PATH)
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return None
    if _cache["path"] == target and _cache["mtime"] == mtime:
        return _cache["model"]
    try:
        model = json.loads(target.read_text())
    except (OSError, ValueError):
        model = None
    _cache.update(path=target, mtime=mtime, model=model)
    return model


def veto_reason(idea: dict[str, Any], model: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Reason to skip an idea, or None. Only active for a ready model that helps."""
    model = model if model is not None else load_model()
    if not model or not model.get("ready") or not model.get("helps"):
        return None
    chance = probability(model["weights"], features(idea, model["vocab"]))
    if chance < float(model.get("threshold", 0.4)):
        return f"Second opinion: only {chance:.0%} chance of a win"
    return None
