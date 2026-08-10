"""Focused checks for repeated-log suppression and monotonic notices."""

import asyncio
import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dist" / "launcher"))
sys.path.insert(0, str(ROOT))

import clientlog
from logfilter import SuppressRepeated
import server


def record(message, level=logging.INFO):
    return logging.LogRecord("test", level, __file__, 1, message, (), None)


class RepeatFilterTests(unittest.TestCase):
    def test_identical_record_is_suppressed(self):
        filter_ = SuppressRepeated(window=10)
        self.assertTrue(filter_.filter(record("same")))
        self.assertFalse(filter_.filter(record("same")))
        self.assertTrue(filter_.filter(record("different")))
        self.assertTrue(filter_.filter(record("same", logging.WARNING)))


class ClientNoticeTests(unittest.TestCase):
    def test_client_notice_uses_background_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("MH3U_LOG_FILE")
            os.environ["MH3U_LOG_FILE"] = str(Path(tmp) / "client.log")
            try:
                log = clientlog.ClientLog()
                log.start_periodic_notice("timer-fired", interval=0.02)
                time.sleep(0.06)
                log.close()
                text = (Path(tmp) / "client.log").read_text(encoding="utf-8")
                self.assertEqual(text.count("timer-fired"), 1)
            finally:
                if old is None:
                    os.environ.pop("MH3U_LOG_FILE", None)
                else:
                    os.environ["MH3U_LOG_FILE"] = old


class ServerNoticeTests(unittest.TestCase):
    def test_server_notice_uses_async_timer(self):
        async def run():
            with mock.patch.object(server.logger, "info") as info:
                task = asyncio.create_task(server.hourly_notice_task(interval=0.01))
                await asyncio.sleep(0.03)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertGreaterEqual(info.call_count, 1)
                info.assert_any_call(server.HOURLY_NOTICE)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
