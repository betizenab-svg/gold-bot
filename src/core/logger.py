from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StructuredLogger:
    def __init__(self, log_path: str | Path = "logs/telemetry.jsonl") -> None:
        self.log_path = Path(log_path)

    def log_pulse_telemetry(self, data_dict: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data_dict) + "\n")
