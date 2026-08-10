"""Tests for the dashboard JSON API (api.py).

Headless and dependency-free: the API module lazily imports the game-state
module (which needs NintendoClients), so without it the endpoints must still
answer 200 with empty/fail-open models — exactly what this test exercises, on
a real loopback HTTP server with ephemeral port. Also checks CORS, OPTIONS,
404s, the log ring buffer, and the status snapshot.

Run:  python tests/test_api.py   (from the mh3u_server/ dir)
"""
import asyncio
import json
import os
import sys
import http.client
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                  # mh3u_server/
sys.path.insert(0, _ROOT)

import api  # noqa: E402


async def _scenario():
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))

    async with api.API("127.0.0.1", 0) as srv:
        port = srv._server.sockets[0].getsockname()[1]
        base = "http://127.0.0.1:%d" % port

        def get(path):
            def _do():
                try:
                    with urllib.request.urlopen(base + path, timeout=5) as r:
                        return r.status, dict(r.headers), json.loads(r.read())
                except urllib.error.HTTPError as e:
                    try:
                        body = json.loads(e.read())
                    except Exception:
                        body = {}
                    return e.code, dict(e.headers), body
            return asyncio.to_thread(_do)

        # --- status -------------------------------------------------------
        st, headers, body = await get("/api/status")
        check(st == 200 and body["server"] == "mh3u-revival", "/api/status shape")
        check(headers.get("Access-Control-Allow-Origin") == "*", "CORS open")
        check(isinstance(body["ports"]["auth"], int)
              and isinstance(body["uptime_s"], (int, float)), "status numeric fields")
        check("caps" in body and "connections" in body["caps"], "status caps")

        # --- players/rooms/halls without the game-state module ------------
        st, _, body = await get("/api/players")
        check(st == 200 and body["count"] == 0 and body["error"], "players fail-open")
        st, _, body = await get("/api/rooms")
        check(st == 200 and body["count"] == 0, "rooms fail-open")
        st, _, body = await get("/api/halls")
        check(st == 200 and body["count"] == 0, "halls fail-open")
        st, _, body = await get("/api/stats")
        check(st == 200 and "by_ip" in body and "reaper" in body, "stats shape")
        check(body["connections"]["max"] == api.limits.MAX_CONNECTIONS, "stats uses limits")

        # --- log ring -----------------------------------------------------
        import logging
        logging.getLogger("mh3u.api-test").info("ring-marker-%d", 1234)
        st, _, body = await get("/api/log?tail=50")
        check(st == 200 and isinstance(body["lines"], list), "/api/log returns lines")
        check(any("ring-marker-1234" in line for line in body["lines"]), "log ring captured")
        st, _, body = await get("/api/log?tail=99999")
        check(len(body["lines"]) <= 500, "/api/log tail capped at 500")

        # --- index / 404 / methods ---------------------------------------
        st, _, body = await get("/api/")
        check(st == 200 and "endpoints" in body and "/api/players" in body["endpoints"],
              "endpoint index")

        # --- webui panel --------------------------------------------------
        st, ct, html = await asyncio.to_thread(
            lambda: (lambda r: (r.status, r.headers.get("Content-Type"), r.read()))(
                urllib.request.urlopen(base + "/panel", timeout=5)))
        check(st == 200 and ct == "text/html; charset=utf-8"
              and b"MH3U Revival" in html and b"setInterval(tick, 3000)" in html,
              "webui panel served")
        st, _, html = await asyncio.to_thread(
            lambda: (lambda r: (r.status, None, r.read()))(
                urllib.request.urlopen(base + "/", timeout=5)))
        check(st == 200 and b"MH3U Revival" in html, "panel at /")

        st, _, _ = await get("/api/nope")
        check(st == 404, "unknown route -> 404")

        def _options():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("OPTIONS", "/api/status")
                resp = conn.getresponse()
                return (resp.status, resp.getheader("Access-Control-Allow-Origin"))
            finally:
                conn.close()
        status, cors = await asyncio.to_thread(_options)
        check(status == 204 and cors == "*", "OPTIONS preflight -> 204 + CORS")

        def _post():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("POST", "/api/status", body=b"x")
                return conn.getresponse().status
            finally:
                conn.close()
        check(await asyncio.to_thread(_post) == 405, "POST -> 405")

    return failures


def test_api_endpoints():
    failures = asyncio.run(_scenario())
    assert not failures, "\n".join(failures)


if __name__ == "__main__":
    import traceback
    try:
        test_api_endpoints()
        print("\nALL tests passed")
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
