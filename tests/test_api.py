"""Tests for the dashboard JSON API (api.py).

Headless and dependency-free: the API module lazily imports the game-state
module (which needs NintendoClients), so without it the endpoints must still
answer 200 with empty/fail-open models — exactly what this test exercises, on
a real loopback HTTP server with ephemeral port. Also checks CORS, OPTIONS,
404s, the privacy rules (no IPs / no logs in any mode), and the status snapshot.

Run:  python tests/test_api.py   (from the mh3u_server/ dir)
"""
import asyncio
import json
import os
import re
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

    # --- privacy: token mode (full vs sanitized) ---------------------------
    old_token = api.API_TOKEN
    api.API_TOKEN = "sekrit-token"
    try:
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

            def get_raw(path, headers=None):
                def _do():
                    req = urllib.request.Request(base + path, headers=headers or {})
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return r.status, dict(r.headers), json.loads(r.read())
                return asyncio.to_thread(_do)

            # no token: access_key hidden
            st, _, body = await get("/api/status")
            check(st == 200 and "access_key" not in body, "no token: access_key hidden")
            # players NEVER carry an ip/plane field, in any mode
            st, _, body = await get("/api/players")
            check(st == 200 and all("ip" not in p and "plane" not in p
                                    for p in body.get("players", [])),
                  "players: no ip/plane even without token")
            st, _, body = await get("/api/players?token=sekrit-token")
            check(st == 200 and all("ip" not in p for p in body.get("players", [])),
                  "players: no ip even WITH token")
            # /api/log is gone entirely
            st, _, _ = await get("/api/log?tail=50")
            check(st == 404, "/api/log removed (404)")
            # stats: no per-IP data
            st, _, body = await get("/api/stats")
            check(st == 200 and "by_ip" not in body, "stats: no by_ip")
            # full (token in query): access_key present
            st, _, body = await get("/api/status?token=sekrit-token")
            check(st == 200 and body.get("access_key") == "cb2b2f5a",
                  "token: access_key visible")
            # full (token header)
            st, _, body = await get_raw("/api/status",
                                        headers={"X-Auth-Token": "sekrit-token"})
            check(st == 200 and "access_key" in body, "token header accepted")
            # wrong token still hides access_key
            st, _, body = await get("/api/status?token=wrong")
            check(st == 200 and "access_key" not in body, "wrong token -> hidden")
    finally:
        api.API_TOKEN = old_token

    # --- no-token mode: loopback is full, remote would be sanitized --------
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
        check(st == 200 and "reaper" in body and "by_ip" not in body, "stats shape (no by_ip)")
        check(body["connections"]["max"] == api.limits.MAX_CONNECTIONS, "stats uses limits")

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


async def _events_check():
    failures = []
    def check(cond, msg):
        if not cond:
            failures.append(msg)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    async with api.API("127.0.0.1", 0) as srv:
        port = srv._server.sockets[0].getsockname()[1]
        def get(path):
            def _do():
                with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=5) as r:
                    return json.loads(r.read())
            return asyncio.to_thread(_do)
        d = await get("/api/events")
        check("seq" in d and isinstance(d.get("events"), list), "/api/events shape")
        check(all(set(e) == {"seq", "type", "pid", "name", "gid"} for e in d["events"]),
              "event fields are privacy-safe (no ip/log)")
        check(await get("/api/events?since=999999999")["events"] if False else True, "placeholder") if False else None
    return failures


def test_events_endpoint():
    failures = asyncio.run(_events_check())
    assert not failures, "\n".join(failures)
