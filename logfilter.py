"""Logging filters shared by the server console, file and dashboard."""

import logging
import os
import threading
import time


class SuppressRepeated(logging.Filter):
    """Drop identical records repeated within a short monotonic-time window."""

    def __init__(self, window=None):
        super().__init__()
        if window is None:
            try:
                window = float(os.environ.get("MH3U_LOG_REPEAT_SECONDS", "60"))
            except (TypeError, ValueError):
                window = 60.0
        self.window = max(0.0, float(window))
        self._seen = {}
        self._lock = threading.Lock()
        self._next_cleanup = 0.0

    def filter(self, record):
        if self.window == 0:
            return True
        now = time.monotonic()
        key = (record.name, record.levelno, record.getMessage())
        with self._lock:
            previous = self._seen.get(key)
            if previous is not None and now - previous < self.window:
                return False
            self._seen[key] = now
            if now >= self._next_cleanup:
                cutoff = now - self.window
                self._seen = {
                    item: timestamp for item, timestamp in self._seen.items()
                    if timestamp >= cutoff
                }
                self._next_cleanup = now + self.window
        return True
