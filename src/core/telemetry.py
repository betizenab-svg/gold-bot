from __future__ import annotations

import logging
import sys
from typing import Any, Optional

_resource: Any = None

try:
    import resource as _resource_mod
    _resource = _resource_mod
except ImportError:  # pragma: no cover - Windows
    pass


class MemoryProfiler:
    def __init__(self, warning_threshold_mb: float = 100.0) -> None:
        self.warning_threshold_mb = warning_threshold_mb

    def get_usage_mb(self) -> Optional[float]:
        if _resource is None:
            return None

        usage = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            # macOS reports bytes
            return usage / (1024 * 1024)
        # Linux reports KB
        return usage / 1024

    def log_snapshot(self, stage_name: str) -> None:
        usage_mb = self.get_usage_mb()
        if usage_mb is None:
            logging.info("%s: Memory usage unavailable on this platform", stage_name)
            return

        logging.info("%s: %.2fMB", stage_name, usage_mb)
        if usage_mb >= self.warning_threshold_mb:
            logging.warning("Memory usage high: %.2fMB", usage_mb)
