#!/usr/bin/env python3
"""Unified client-side logging for the MH3U launcher.

Every launcher line gets a timestamp + level, is persisted to a size-capped
rotating file next to the launcher, and can be streamed to registered
listeners (the GUI log panes). Thread-safe: the launcher logs from the GUI
thread, worker threads, the server-log reader and the easytier pump.

Rotation is manual (close -> rename chain -> reopen) instead of the logging
module's RotatingFileHandler, because that renames the file while it is still
open — legal on POSIX, but Windows refuses with WinError 32. This is the
launcher's own writer, so it controls the only handle and can close first.

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
import re
import sys
import time
import threading


HOURLY_NOTICE = "怪物猎人通讯部的小偷与土皇帝不得入内。"
HOURLY_NOTICE_SECONDS = 60.0 * 60.0
REPEAT_SUPPRESSION_SECONDS = 60.0

_FORWARDED_PREFIX = re.compile(
    r"^(?:\[server\]\s*)?(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})?\s+)?(?:INFO|WARNING|WARN|ERROR|DEBUG)\s+(?:[^:]+:\s*)?"
)


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
        self._file = None
        self._path = None
        self._notice_stop = threading.Event()
        self._notice_thread = None
        self._latest_notice_line = None
        self._recent = {}
        self._recent_cleanup = 0.0

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
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._backups = max(1, backups)

        log_name = os.environ.get("MH3U_LOG_FILE", "client.log")
        if log_name:
            self._path = (log_name if os.path.isabs(log_name)
                          else os.path.join(_log_dir(), log_name))
            try:
                self._file = open(self._path, "a", encoding="utf-8")
            except OSError as e:
                print("WARNING: could not open client log %r (%s) - console only"
                      % (self._path, e), file=sys.stderr)
                self._path = None
                self._file = None

    # -- plumbing -----------------------------------------------------------
    def _fmt(self, level, msg):
        return "%s %-7s %s" % (time.strftime("%H:%M:%S"), level, msg)

    def _rotate(self):
        """Windows-safe rotation: close the handle first, shift the chain
        (file -> .1 -> .2 ...), then reopen fresh."""
        try:
            if self._file is not None:
                self._file.close()
        except Exception:
            pass
        base = self._path
        try:
            for i in range(self._backups - 1, 0, -1):
                src, dst = "%s.%d" % (base, i), "%s.%d" % (base, i + 1)
                try:
                    os.replace(src, dst)
                except OSError:
                    pass
            os.replace(base, base + ".1")
        except OSError:
            pass
        try:
            self._file = open(base, "a", encoding="utf-8")
        except OSError:
            self._path = None
            self._file = None

    def _emit(self, level, msg):
        now = time.monotonic()
        with self._lock:
            # Forwarded server/tool lines carry their own changing timestamps.
            # Ignore that envelope so the same payload is still recognized.
            key = (level, _FORWARDED_PREFIX.sub("", msg, count=1))
            previous = self._recent.get(key)
            if (previous is not None
                    and now - previous < REPEAT_SUPPRESSION_SECONDS):
                return None
            self._recent[key] = now
            if now >= self._recent_cleanup:
                cutoff = now - REPEAT_SUPPRESSION_SECONDS
                self._recent = {
                    item: timestamp for item, timestamp in self._recent.items()
                    if timestamp >= cutoff
                }
                self._recent_cleanup = now + REPEAT_SUPPRESSION_SECONDS
            line = self._fmt(level, msg)
            if self._file is not None:
                try:
                    self._file.write(line + "\n")
                    self._file.flush()
                    if self._file.tell() > self._max_bytes:
                        self._rotate()
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

    def start_periodic_notice(self, message=HOURLY_NOTICE,
                              interval=HOURLY_NOTICE_SECONDS):
        """Emit once now, then once per elapsed interval."""
        if self._notice_thread is not None and self._notice_thread.is_alive():
            return
        interval = float(interval)
        if interval <= 0:
            raise ValueError("periodic notice interval must be positive")

        self._latest_notice_line = self.info(message)

        def run():
            deadline = time.monotonic() + interval
            while not self._notice_stop.wait(max(0.0, deadline - time.monotonic())):
                line = self.info(message)
                if line is not None:
                    self._latest_notice_line = line
                deadline += interval
                if deadline <= time.monotonic():
                    deadline = time.monotonic() + interval

        self._notice_stop.clear()
        self._notice_thread = threading.Thread(
            target=run, name="mh3u-hourly-notice", daemon=True)
        self._notice_thread.start()

    def latest_notice_line(self):
        """Return the already-persisted notice for initial GUI display."""
        return self._latest_notice_line

    def close(self):
        """Stop the notice timer and close the current log file."""
        self._notice_stop.set()
        thread = self._notice_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None


_log = None
_log_lock = threading.Lock()


def get_logger():
    """Process-wide singleton ClientLog."""
    global _log
    with _log_lock:
        if _log is None:
            _log = ClientLog()
            _log.start_periodic_notice()
        return _log
