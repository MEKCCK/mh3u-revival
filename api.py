"""HTTP JSON data API for the MH3U server — feeds the webui dashboard panel.

A tiny stdlib asyncio HTTP/1.1 server (GET/OPTIONS only) exposing the live
game state as JSON: connections, hunt rooms, gathering halls, occupancy caps
and recent log lines. No third-party deps — it runs inside the server's
event loop (one coroutine; one reader/writer pair per request).

Design rules (mirroring the server's fail-open style):
  * Every snapshot is defensive — an internal error yields partial data with
    an "error" key, never a crash, and never blocks a game handler.
  * The game-state module (matchmaking_handlers) is imported lazily and
    guarded: without it (unit tests, degraded boot) the endpoints still
    answer with empty models, so the panel can be tested headless.
  * CORS-open (Access-Control-Allow-Origin: *) so a browser panel on another
    machine can call it directly.

Env knobs (all optional):
  MH3U_API=0        disable the API server entirely (default: on)
  MH3U_API_PORT     listen port            (default 1623)
  MH3U_API_BIND     bind address           (default 127.0.0.1;
                     a LAN/remote panel: set 0.0.0.0 — note it exposes
                     player IPs and room data to the LAN)
  MH3U_API_LOG      "on"/"off" for the log ring buffer (default on)

Endpoints:
  GET /api/status   server identity, topology, advertised address, caps, uptime
  GET /api/players  live secure connections (pid, name, ip, plane, uptime, idle,
                    room/hall memberships)
  GET /api/rooms    hunt rooms (gid, host, participants, attribs, app-buffer len)
  GET /api/halls    gathering halls + lobbies (gid, name, owner, population)
  GET /api/stats    occupancy, caps, per-IP connections, rate-limit state
  GET /api/log      recent log lines (?tail=N, capped)
  GET /api/         endpoint index
"""
import os
import json
import time
import asyncio
import logging
import threading
import collections

import config
import limits
import webui

logger = logging.getLogger("mh3u.api")

API_PORT = limits._int_env("MH3U_API_PORT", 1623)
API_BIND = os.environ.get("MH3U_API_BIND", "127.0.0.1")
API_ENABLED = os.environ.get("MH3U_API", "1") != "0"
API_LOG_RING = os.environ.get("MH3U_API_LOG", "on") != "off"

_STARTED_AT = time.time()

# ---------------------------------------------------------------------------
# Log ring buffer — /api/log serves the last N formatted lines.
# ---------------------------------------------------------------------------

class _RingHandler(logging.Handler):
    """Captures formatted log lines (INFO+) into a bounded ring buffer."""

    def __init__(self, cap=800):
        super().__init__(level=logging.INFO)
        self._buf = collections.deque(maxlen=cap)
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            line = self.format(record)
            with self._lock:
                self._buf.append(line)
        except Exception:
            pass

    def tail(self, n):
        with self._lock:
            return list(self._buf)[-n:]


_ring = _RingHandler()
_ring_attached = False


def _ensure_ring():
    """Attach the ring handler to the root logger exactly once."""
    global _ring_attached
    if _ring_attached or not API_LOG_RING:
        return
    _ring_attached = True
    # The ring captures INFO+; make sure the root logger's own level doesn't
    # gate records before they reach our handler (the server's basicConfig
    # already sets INFO; standalone use must too).
    root = logging.getLogger()
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    root.addHandler(_ring)


# ---------------------------------------------------------------------------
# State snapshots (defensive; fail-open to empty models)
# ---------------------------------------------------------------------------

def _mh():
    """The live game-state module, or None when unavailable (tests/degraded)."""
    try:
        import matchmaking_handlers as m
        return m
    except Exception:
        return None


def _client_ip(client):
    return getattr(client, "_mh3u_ip", None) or limits.remote_ip(client)


def snapshot_status():
    m = _mh()
    out = {
        "server": "mh3u-revival",
        "game_server_id": hex(config.GAME_SERVER_ID),
        "nex_version": config.NEX_VERSION,
        "access_key": config.ACCESS_KEY,
        "bind": config.HOST,
        "ports": {
            "auth": config.AUTH_PORT,
            "secure": config.SECURE_PORT,
            "natcheck": limits._int_env("MH3U_NATCHECK_PORT", 10025),
            "api": API_PORT,
        },
        "advertised_address": config.SERVER_ADDRESS,
        "advertise_override": config.ADVERTISE_ADDRESS or None,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(_STARTED_AT)),
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "caps": {
            "rooms": limits.MAX_ROOMS,
            "room_participants": limits.MAX_ROOM_PARTICIPANTS,
            "connections": limits.MAX_CONNECTIONS,
            "per_ip": limits.MAX_CONNECTIONS_PER_IP,
            "runtime_communities": limits.MAX_RUNTIME_COMMUNITIES,
        },
    }
    if m is not None:
        out["halls"] = {
            "hall_max": getattr(m, "HALL_MAX", None),
            "room_max": getattr(m, "ROOM_MAX", None),
            "num_worlds": getattr(m, "NUM_WORLDS", None),
        }
        out["password_room_policy"] = {
            "enabled": getattr(m, "DESTROY_PASSWORD_ROOMS", False),
            "destroyed": getattr(m.REGISTRY, "password_rooms_destroyed", 0),
            "scan_seconds": getattr(m, "PASSWORD_ROOM_SCAN_SECONDS", None),
            "attribute_index": getattr(m, "PASSWORD_ATTR_INDEX", None),
        }
    return out


def snapshot_players():
    m = _mh()
    if m is None:
        return {"players": [], "count": 0, "error": "state unavailable"}
    now = time.monotonic()
    out = []
    for pid, client in list(m.CLIENTS.items()):
        ip = _client_ip(client)
        last = getattr(client, "_mh3u_last_rx", None)
        conn_at = getattr(client, "_mh3u_connected_at", None)
        rooms = [gid for gid, s in m.REGISTRY.sessions.items()
                 if pid in s.participants]
        halls = [gid for gid, c in m.COMMUNITY.communities.items()
                 if pid in c.participants]
        out.append({
            "pid": pid,
            "name": m.NAMES.get(pid),
            "ip": ip,
            "plane": limits.plane_name(ip) if ip else "unknown",
            "cid": getattr(client, "_mh3u_cid", None),
            "uptime_s": round(now - conn_at, 1) if conn_at else None,
            "idle_s": round(now - last, 1) if last else None,
            "rooms": [hex(g) for g in rooms],
            "halls": [hex(g) for g in halls],
        })
    out.sort(key=lambda p: p["pid"])
    return {"players": out, "count": len(out)}


def snapshot_rooms():
    m = _mh()
    if m is None:
        return {"rooms": [], "count": 0, "error": "state unavailable"}
    out = []
    for gid, s in sorted(m.REGISTRY.sessions.items()):
        g = s.gathering
        out.append({
            "gid": hex(gid),
            "host_pid": s.host_pid,
            "host_name": m.NAMES.get(s.host_pid),
            "num_participants": len(s.participants),
            "max_participants": getattr(g, "max_participants", None),
            "game_mode": getattr(g, "game_mode", None),
            "attribs": list(getattr(g, "attribs", []) or []),
            "application_data_len": len(getattr(g, "application_data", b"") or b""),
            "participants": [
                {"pid": p, "name": m.NAMES.get(p)} for p in sorted(s.participants)],
        })
    return {"rooms": out, "count": len(out)}


def _hall_name(pg):
    """Hall display name — the wire name is ':'-packed per-language; show the
    first segment for the panel."""
    name = getattr(pg, "description", "") or ""
    return str(name).split(":")[0] or str(name)


def snapshot_halls():
    m = _mh()
    if m is None:
        return {"halls": [], "count": 0, "error": "state unavailable"}
    out = []
    for gid, c in sorted(m.COMMUNITY.communities.items()):
        pg = c.pg
        out.append({
            "gid": hex(gid),
            "name": _hall_name(pg),
            "owner_pid": getattr(pg, "owner", None),
            "official": bool(c.official),
            "is_lobby": gid in getattr(m.COMMUNITY, "lobbies", {}).values(),
            "num_participants": len(c.participants),
            "displayed_population": getattr(pg, "num_participants", None),
            "max_participants": getattr(pg, "max_participants", None),
            "participants": [
                {"pid": p, "name": m.NAMES.get(p)} for p in sorted(c.participants)],
        })
    return {"halls": out, "count": len(out)}


def snapshot_stats():
    m = _mh()
    now = time.monotonic()
    out = {
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "connections": {
            "current": len(m.CLIENTS) if m is not None else 0,
            "max": limits.MAX_CONNECTIONS,
            "per_ip_max": limits.MAX_CONNECTIONS_PER_IP,
        },
        "rooms": {
            "current": len(m.REGISTRY.sessions) if m is not None else 0,
            "max": limits.MAX_ROOMS,
        },
        "halls": {
            "current": len(m.COMMUNITY.communities) if m is not None else 0,
            "runtime": len(m.COMMUNITY._runtime_gids) if m is not None else 0,
            "max": limits.MAX_RUNTIME_COMMUNITIES,
        },
        "by_ip": [{"ip": ip, "connections": n}
                  for ip, n in sorted(limits._ip_conns.items())],
        "shout_tracked_pids": len(limits._shout_buckets),
        "shout_rate": {"per_sec": limits.SHOUTS_PER_SEC, "burst": limits.SHOUT_BURST},
    }
    try:
        import reaper
        out["reaper"] = {"timeout_s": reaper.REAP_TIMEOUT,
                         "interval_s": reaper.REAP_INTERVAL}
    except Exception:
        out["reaper"] = {"timeout_s": None, "interval_s": None}
    return out


def snapshot_log(tail):
    try:
        n = max(0, min(int(tail), 500))
    except (TypeError, ValueError):
        n = 200
    return {"lines": _ring.tail(n)}


# ---------------------------------------------------------------------------
# Minimal HTTP/1.1 plumbing (GET/OPTIONS, JSON, CORS-open)
# ---------------------------------------------------------------------------

_ROUTES = {
    "/api/status": ("status", snapshot_status),
    "/api/players": ("players", snapshot_players),
    "/api/rooms": ("rooms", snapshot_rooms),
    "/api/halls": ("halls", snapshot_halls),
    "/api/stats": ("stats", snapshot_stats),
    "/api/log": ("log", snapshot_log),
}

# The built-in webui panel (self-contained HTML, zero external deps).
_PANEL_PATHS = ("/", "/panel", "/webui")


def _json_response(writer, code, payload, extra_headers=()):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if code == 204:
        body = b""
    status = {200: "OK", 204: "No Content", 404: "Not Found",
              405: "Method Not Allowed", 500: "Internal Server Error"}
    headers = (
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        "Content-Length: %d\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Access-Control-Allow-Methods: GET, OPTIONS\r\n"
        "Access-Control-Allow-Headers: Content-Type\r\n"
        "Connection: close\r\n"
        "%s\r\n" % (code, status.get(code, "Error"), len(body),
                    "".join("%s: %s\r\n" % h for h in extra_headers))
    ).encode("ascii")
    try:
        writer.write(headers + body)
    except Exception:
        pass


def _html_response(writer, code, html):
    body = html.encode("utf-8")
    headers = (
        "HTTP/1.1 %d OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Length: %d\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n"
        "\r\n" % (code, len(body))
    ).encode("ascii")
    try:
        writer.write(headers + body)
    except Exception:
        pass


async def _handle(reader, writer):
    try:
        # Read the request head (line + headers), cap it so a junk client
        # can't hold the connection forever.
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 16384:
            chunk = await asyncio.wait_for(reader.read(2048), timeout=10)
            if not chunk:
                return
            head += chunk
        lines = head.decode("latin-1", "replace").split("\r\n")
        method, path, _ver = (lines[0].split(" ", 2) + ["", ""])[:3]

        if method == "OPTIONS":
            _json_response(writer, 204, {}, ())
            return
        if method != "GET":
            _json_response(writer, 405, {"error": "method not allowed"})
            return

        route_path, _, query = path.partition("?")
        if route_path in _PANEL_PATHS:
            _html_response(writer, 200, webui.WEBUI_HTML)
            return
        if route_path == "/api/":
            _json_response(writer, 200, {
                "endpoints": sorted(_ROUTES),
                "panel": "/panel",
                "hint": "/api/log?tail=200",
            })
            return
        entry = _ROUTES.get(route_path)
        if entry is None:
            _json_response(writer, 404, {"error": "no such endpoint: %s" % route_path})
            return
        _name, fn = entry
        params = {}
        for pair in query.split("&") if query else []:
            if "=" in pair:
                k, _, v = pair.partition("=")
                params[k] = v
        try:
            if _name == "log":
                payload = fn(params.get("tail", "200"))
            else:
                payload = fn()
            _json_response(writer, 200, payload)
        except Exception as e:
            logger.exception("api: %s failed: %s", route_path, e)
            _json_response(writer, 500, {"error": "internal error", "detail": str(e)})
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    except Exception:
        pass
    finally:
        try:
            await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass


async def _client_connected(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        await _handle(reader, writer)
    finally:
        if peer and logger.isEnabledFor(logging.DEBUG):
            logger.debug("api: request done from %s", peer)


class API:
    """Async context manager for the API server (entered inside the loop)."""

    def __init__(self, host=API_BIND, port=API_PORT):
        self.host = host
        self.port = port
        self._server = None

    async def __aenter__(self):
        _ensure_ring()
        self._server = await asyncio.start_server(
            _client_connected, self.host, self.port)
        logger.info("api: dashboard JSON API on http://%s:%d/api/ (bind %s:%d)",
                    "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host,
                    self.port, self.host, self.port)
        return self

    async def __aexit__(self, *exc):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


def serve(host=API_BIND, port=API_PORT):
    """Convenience async context manager with env-driven defaults."""
    return API(host, port)
