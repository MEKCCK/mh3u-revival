#!/usr/bin/env python3
"""Unified client-side logging for the MH3U launcher.

Every launcher line gets a timestamp + level, is persisted to a size-capped
rotating file next to the launcher, and can be streamed to registered
listeners (the GUI log panes). Thread-safe: the launcher logs from the GUI
thread, worker threads, the server-log reader and the easytier pump.

The env knobs mirror the server's logging exactly, so a host can point both
sides at the same naming convention:
  MH3U_LOG_FILE      filename or absolute path; "" disables the file
  MH3U_LOG_MAX_MB    per-file cap (default 2)
  MH3U_LOG_BACKUPS   rotating files kept (default 3)

Usage:
  import clientlog
  clog = clientlog.get_logger()
  line = clog.info("server started")      # returns the formatted line
  clog.warning("mesh failed: %s", why)
  clog.add_listener(cb)                   # cb(formatted_line)
"""
import os
import sys
import time
import threading
import logging
from logging.handlers import RotatingFileHandler


def _log_dir():
    """Frozen (PyInstaller onefile): next to the exe (bundle root); else here."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


class ClientLog:
    """Leveled, timestamped, rotating-file launcher log with listeners."""

    def __init__(self, max_mb=None, backups=None):
        self._lock = threading.Lock()
        self._listeners = []
        self._handler = None

        if max_mb is None:
            try:
                max_mb = float(os.environ.get("MH3U_LOG_MAX_MB", "2"))
            except (TypeError, ValueError):
                max_mb = 2.0
        if backups is None:
            try:
                backups = int(os.environ.get("MH3U_LOG_BACKUPS", "3"))
            except (TypeError, ValueError):
                backups = 3

        log_name = os.environ.get("MH3U_LOG_FILE", "client.log")
        if log_name:
            path = log_name if os.path.isabs(log_name) else os.path.join(_log_dir(), log_name)
            try:
                handler = RotatingFileHandler(
                    path, maxBytes=int(max_mb * 1024 * 1024),
                    backupCount=backups, encoding="utf-8")
                # The line already carries timestamp + level.
                handler.setFormatter(logging.Formatter("%(message)s"))
                self._handler = handler
            except OSError as e:
                print("WARNING: could not open client log %r (%s) - console only"
                      % (path, e), file=sys.stderr)

    # -- plumbing -----------------------------------------------------------
    def _fmt(self, level, msg):
        return "%s %-7s %s" % (time.strftime("%H:%M:%S"), level, msg)

    def _emit(self, level, msg):
        line = self._fmt(level, msg)
        with self._lock:
            if self._handler is not None:
                try:
                    rec = logging.LogRecord(
                        "mh3u.launcher", logging.INFO, "", 0, line, None, None)
                    self._handler.emit(rec)
                except Exception:
                    pass
            for cb in list(self._listeners):
                try:
                    cb(line)
                except Exception:
                    pass
        return line

    # -- public API ---------------------------------------------------------
    def info(self, msg, *args):
        return self._emit("INFO", msg % args if args else msg)

    def warning(self, msg, *args):
        return self._emit("WARNING", msg % args if args else msg)

    def error(self, msg, *args):
        return self._emit("ERROR", msg % args if args else msg)

    def add_listener(self, cb):
        """Register cb(formatted_line) — called on every emitted line."""
        with self._lock:
            self._listeners.append(cb)

    def remove_listener(self, cb):
        with self._lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass


_log = None
_log_lock = threading.Lock()


def get_logger():
    """Process-wide singleton ClientLog."""
    global _log
    with _log_lock:
        if _log is None:
            _log = ClientLog()
        return _log
