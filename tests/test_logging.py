"""Focused checks for repeated-log suppression and monotonic notices."""

import asyncio
import io
import logging
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dist" / "launcher"))
sys.path.insert(0, str(ROOT))

import clientlog
import easytier
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
    def test_client_notice_emits_immediately_and_uses_background_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("MH3U_LOG_FILE")
            os.environ["MH3U_LOG_FILE"] = str(Path(tmp) / "client.log")
            try:
                log = clientlog.ClientLog()
                log.start_periodic_notice("timer-fired", interval=0.02)
                first = (Path(tmp) / "client.log").read_text(encoding="utf-8")
                self.assertEqual(first.count("timer-fired"), 1)
                self.assertIn("timer-fired", log.latest_notice_line())
                time.sleep(0.06)
                log.close()
                text = (Path(tmp) / "client.log").read_text(encoding="utf-8")
                self.assertEqual(text.count("timer-fired"), 1)
            finally:
                if old is None:
                    os.environ.pop("MH3U_LOG_FILE", None)
                else:
                    os.environ["MH3U_LOG_FILE"] = old

    def test_forwarded_timestamps_do_not_defeat_duplicate_suppression(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.log"
            with mock.patch.dict(os.environ, {"MH3U_LOG_FILE": str(path)}):
                log = clientlog.ClientLog()
                log.info("[server] 2026-08-10 10:00:00 INFO mh3u: same payload")
                log.info("[server] 2026-08-10 10:00:01 INFO mh3u: same payload")
                log.close()
            self.assertEqual(path.read_text(encoding="utf-8").count("same payload"), 1)


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

    def test_notice_text_is_exact(self):
        expected = "怪物猎人通讯部的小偷与土皇帝不得入内。"
        self.assertEqual(server.HOURLY_NOTICE, expected)
        self.assertEqual(clientlog.HOURLY_NOTICE, expected)


class EasyTierLogTests(unittest.TestCase):
    SAMPLE = (
        "2026-08-10T23:27:18.6592708+08:00 ERROR "
        "easytier::common::dns: system dns lookup failed\n"
        "Caused by:\n"
        "    proto error: no records found for Query\n"
    )

    def make_net(self, sink):
        net = object.__new__(easytier.EasyTierNet)
        net._proc = types.SimpleNamespace(stdout=io.StringIO(self.SAMPLE))
        net.log = sink.append
        return net

    def test_raw_core_retries_are_not_persisted_by_default(self):
        sink = []
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MH3U_EASYTIER_VERBOSE", None)
            self.make_net(sink)._pump()
        self.assertEqual(sink, [])

    def test_raw_core_output_can_be_enabled_for_diagnostics(self):
        sink = []
        with mock.patch.dict(os.environ, {"MH3U_EASYTIER_VERBOSE": "1"}):
            self.make_net(sink)._pump()
        self.assertEqual(len(sink), 3)
        self.assertTrue(all(line.startswith("[easytier] ") for line in sink))


if __name__ == "__main__":
    unittest.main()
