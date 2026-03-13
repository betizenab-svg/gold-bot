from __future__ import annotations

import logging
import sys
import time
from typing import Any, Optional

_resource: Any = None

try:
    import resource as _resource_mod

    _resource = _resource_mod
except ImportError:  # pragma: no cover - Windows
    _resource = None


class MemoryProfiler:
    def __init__(self, warning_threshold_mb: float = 100.0) -> None:
        self.warning_threshold_mb = warning_threshold_mb
        self._started_at: Optional[float] = None

    def start_timer(self) -> None:
        self._started_at = time.perf_counter()

    def stop_timer(self) -> float:
        if self._started_at is None:
            return 0.0
        elapsed_seconds = time.perf_counter() - self._started_at
        return elapsed_seconds * 1000.0

    def get_peak_memory_mb(self) -> float:
        if _resource is None:
            return 0.0

        usage = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return float(usage) / (1024.0 * 1024.0)
        return float(usage) / 1024.0

    def get_usage_mb(self) -> Optional[float]:
        if _resource is None:
            return None
        return self.get_peak_memory_mb()

    def log_snapshot(self, stage_name: str) -> None:
        usage_mb = self.get_usage_mb()
        if usage_mb is None:
            logging.info("%s: Memory usage unavailable on this platform", stage_name)
            return

        logging.info("%s: %.2fMB", stage_name, usage_mb)
        if usage_mb >= self.warning_threshold_mb:
            logging.warning("Memory usage high: %.2fMB", usage_mb)
