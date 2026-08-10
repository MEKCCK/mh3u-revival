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
  GET /api/players  live connections (pid, name, uptime, idle, room/hall
                    memberships) — NO IP addresses, ever
  GET /api/rooms    hunt rooms (gid, host, participants, attribs, app-buffer len)
  GET /api/halls    gathering halls (gid, name, owner, population)
  GET /api/stats    occupancy, caps, rate-limit state — NO per-IP data
  GET /api/         endpoint index

PRIVACY: no IP addresses and no log lines are exposed through the API or the
panel — in any mode, for any caller. The only token-gated fields are
/api/status access_key and /api/players cid.
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

# Privacy: the API/panel may be publicly reachable (MH3U_API_BIND=0.0.0.0).
# IP addresses and logs are NOT exposed AT ALL — never in any mode. Only
# these stay gated behind the operator token / loopback:
#   * /api/status access_key
#   * /api/players cid (internal connection id)
# Setting MH3U_API_TOKEN makes full detail available from anywhere to
# requests carrying the token (?token=... or X-Auth-Token header).
API_TOKEN = os.environ.get("MH3U_API_TOKEN", "").strip()

_STARTED_AT = time.time()


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


def snapshot_status(full=True):
    m = _mh()
    out = {
        "server": "mh3u-revival",
        "game_server_id": hex(config.GAME_SERVER_ID),
        "nex_version": config.NEX_VERSION,
        "bind": config.HOST,
        "ports": {
            "auth": config.AUTH_PORT,
            "secure": config.SECURE_PORT,
            "natcheck": limits._int_env("MH3U_NATCHECK_PORT", 10025),
            "api": API_PORT,
        },
        "advertised_address": config.SERVER_ADDRESS,
        "advertise_override": config.ADVERTISE_ADDRESS or None,
        # what players actually type into the launcher (host-shared address)
        "public_address": config.PUBLIC_ADDRESS or None,
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
    if full:
        out["access_key"] = config.ACCESS_KEY
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


def snapshot_players(full=True):
    m = _mh()
    if m is None:
        return {"players": [], "count": 0, "error": "state unavailable"}
    now = time.monotonic()
    out = []
    for pid, client in list(m.CLIENTS.items()):
        last = getattr(client, "_mh3u_last_rx", None)
        conn_at = getattr(client, "_mh3u_connected_at", None)
        rooms = [gid for gid, s in m.REGISTRY.sessions.items()
                 if pid in s.participants]
        # A player in a port joins BOTH the world community AND its paired
        # lobby — the lobby is game plumbing, never surface it to the panel.
        lobby_gids = set(getattr(m.COMMUNITY, "lobbies", {}).values())
        halls = [gid for gid, c in m.COMMUNITY.communities.items()
                 if pid in c.participants and gid not in lobby_gids]
        entry = {
            "pid": pid,
            "name": m.NAMES.get(pid),
            "uptime_s": round(now - conn_at, 1) if conn_at else None,
            "idle_s": round(now - last, 1) if last else None,
            "rooms": [hex(g) for g in rooms],
            "halls": [hex(g) for g in halls],
        }
        if full:
            entry["cid"] = getattr(client, "_mh3u_cid", None)
        out.append(entry)
    out.sort(key=lambda p: p["pid"])
    return {"players": out, "count": len(out)}


def snapshot_rooms(full=True):
    m = _mh()
    if m is None:
        return {"rooms": [], "count": 0, "error": "state unavailable"}
    out = []
    for gid, s in sorted(m.REGISTRY.sessions.items()):
        g = s.gathering
        out.append({
            "gid": hex(gid),
            "name": getattr(s, "name", "") or getattr(g, "description", "") or None,
            "host_pid": s.host_pid,
            "host_name": m.NAMES.get(s.host_pid),
            "num_participants": len(s.participants),
            "max_participants": getattr(g, "max_participants", None),
            "game_mode": getattr(g, "game_mode", None),
            "attribs": list(getattr(g, "attribs", []) or []),
            "application_data_len": len(getattr(g, "application_data", b"") or b""),
            "participants": [
                {"pid": p, "name": m.NAMES.get(p)}
                for p in sorted(s.participants)],
        })
    return {"rooms": out, "count": len(out)}


def _hall_name(pg):
    """Hall display name — the wire name is ':'-packed per-language; show the
    first segment for the panel."""
    name = getattr(pg, "description", "") or ""
    return str(name).split(":")[0] or str(name)


def snapshot_halls(full=True):
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
            # Wire max = HALL_MAX + display offset (2 for ports, 1 for lobbies);
            # the GAME renders max - offset, so expose the player-visible cap.
            "displayed_max": (getattr(pg, "max_participants", None)
                              - getattr(c, "offset", 0)
                              if getattr(c, "offset", 0) else getattr(pg, "max_participants", None)),
            "max_participants": getattr(pg, "max_participants", None),
            "participants": [
                {"pid": p, "name": m.NAMES.get(p)}
                for p in sorted(c.participants)],
        })
    return {"halls": out, "count": len(out)}


def snapshot_stats(full=True):
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


# ---------------------------------------------------------------------------
# Minimal HTTP/1.1 plumbing (GET/OPTIONS, JSON, CORS-open)
# ---------------------------------------------------------------------------

# Activity event feed — /api/events (privacy-safe: pid/name/gid only)
# ---------------------------------------------------------------------------

_event_log = collections.deque(maxlen=50)   # (seq, type, pid, name, gid)
_event_seq = 0
_event_lock = threading.Lock()


def _push_event(etype, pid, name, gid):
    global _event_seq
    with _event_lock:
        _event_seq += 1
        _event_log.append((_event_seq, etype, pid, name, gid))


def snapshot_events(since):
    try:
        s = int(since or 0)
    except (TypeError, ValueError):
        s = 0
    with _event_lock:
        items = [e for e in _event_log if e[0] > s]
        seq = _event_seq
    return {
        "seq": seq,
        "events": [{"seq": e[0], "type": e[1], "pid": e[2],
                    "name": e[3], "gid": e[4]} for e in items],
    }


async def _event_watcher():
    """Background task: diff live state every 2s and push join/leave/create
    events. Pure membership data — never IPs or log lines."""
    prev_players, prev_rooms, prev_ports = set(), {}, {}
    while True:
        await asyncio.sleep(2)
        try:
            m = _mh()
            if m is None:
                continue
            now_players = set(m.CLIENTS)
            now_rooms = {gid: set(s.participants)
                         for gid, s in m.REGISTRY.sessions.items()}
            now_ports = {gid: set(c.participants)
                         for gid, c in m.COMMUNITY.communities.items()
                         if gid not in getattr(m.COMMUNITY, "lobbies", {}).values()}

            for pid in sorted(now_players - prev_players):
                _push_event("player_joined", pid, m.NAMES.get(pid), None)
            for pid in sorted(prev_players - now_players):
                _push_event("player_left", pid, None, None)
            for gid in sorted(now_rooms.keys() - prev_rooms.keys()):
                s = m.REGISTRY.sessions[gid]
                _push_event("room_created", s.host_pid, m.NAMES.get(s.host_pid), hex(gid))
            for gid in sorted(prev_rooms.keys() - now_rooms.keys()):
                _push_event("room_destroyed", None, None, hex(gid))
            for gid, parts in now_rooms.items():
                for pid in sorted(parts - prev_rooms.get(gid, set())):
                    _push_event("room_joined", pid, m.NAMES.get(pid), hex(gid))
                for pid in sorted(prev_rooms.get(gid, set()) - parts):
                    _push_event("room_left", pid, m.NAMES.get(pid), hex(gid))
            for gid, parts in now_ports.items():
                for pid in sorted(parts - prev_ports.get(gid, set())):
                    _push_event("port_joined", pid, m.NAMES.get(pid), hex(gid))
                for pid in sorted(prev_ports.get(gid, set()) - parts):
                    _push_event("port_left", pid, m.NAMES.get(pid), hex(gid))

            prev_players, prev_rooms, prev_ports = now_players, now_rooms, now_ports
        except Exception:
            pass
# ---------------------------------------------------------------------------
# Server resource metrics (/api/system) — /proc-based, zero deps, POSIX only
# ---------------------------------------------------------------------------

_sys_last = {}


def _read_mem():
    try:
        d = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                d[k.strip()] = int(v.split()[0]) * 1024
        total = d.get("MemTotal", 0)
        avail = d.get("MemAvailable", 0)
        return {"used": total - avail, "total": total} if total else None
    except Exception:
        return None


def _read_cpu():
    try:
        with open("/proc/stat") as f:
            nums = [int(x) for x in f.readline().split()[1:]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)   # idle + iowait
        return {"idle": idle, "total": sum(nums)}
    except Exception:
        return None


def _read_net():
    try:
        rx = tx = 0
        with open("/proc/net/dev") as f:
            next(f)
            next(f)
            for line in f:
                p = line.split()
                if len(p) >= 10:
                    rx += int(p[1])
                    tx += int(p[9])
        return {"rx": rx, "tx": tx}
    except Exception:
        return None


def snapshot_system(full=True):
    """CPU% / memory / network rates, sampled as deltas between requests
    (the panel polls every 3s). None fields on non-POSIX hosts."""
    out = {"memory": None, "cpu_percent": None,
           "net_rx_bps": None, "net_tx_bps": None}
    mem = _read_mem()
    cpu = _read_cpu()
    net = _read_net()
    if mem:
        out["memory"] = mem
    now = time.monotonic()
    last = _sys_last
    if cpu and net and last:
        dt = now - last.get("t", now)
        if dt >= 0.5:
            dt_cpu = cpu["total"] - last.get("cpu_total", cpu["total"])
            if dt_cpu > 0:
                out["cpu_percent"] = round(
                    100.0 * (1 - (cpu["idle"] - last.get("cpu_idle", cpu["idle"])) / dt_cpu), 1)
            out["net_rx_bps"] = int(max(0, net["rx"] - last.get("net_rx", net["rx"])) / dt)
            out["net_tx_bps"] = int(max(0, net["tx"] - last.get("net_tx", net["tx"])) / dt)
    _sys_last.update({"t": now,
                      "cpu_idle": cpu["idle"] if cpu else 0,
                      "cpu_total": cpu["total"] if cpu else 0,
                      "net_rx": net["rx"] if net else 0,
                      "net_tx": net["tx"] if net else 0})
    return out


_ROUTES = {
    "/api/status": ("status", snapshot_status),
    "/api/players": ("players", snapshot_players),
    "/api/rooms": ("rooms", snapshot_rooms),
    "/api/halls": ("halls", snapshot_halls),
    "/api/stats": ("stats", snapshot_stats),
    "/api/events": ("events", snapshot_events),
    "/api/system": ("system", snapshot_system),
}

# The built-in webui panel (self-contained HTML, zero external deps).
_PANEL_PATHS = ("/", "/panel", "/webui")


def _header_value(head, name):
    """Case-insensitive header lookup on the raw request head bytes."""
    want = (name + ":").lower()
    for line in head.decode("latin-1", "replace").split("\r\n"):
        if line.lower().startswith(want):
            return line.split(":", 1)[1].strip()
    return ""


def _is_full_access(params, head, writer):
    """Full detail vs sanitized:

      * If MH3U_API_TOKEN is configured: full only when the request carries
        the matching token (?token=... or X-Auth-Token header).
      * Otherwise: full only for loopback callers (the operator's shell).
    Everything else gets sanitized data — no IPs, no names, redacted logs.
    """
    if API_TOKEN:
        token = params.get("token") or _header_value(head, "x-auth-token")
        return bool(token) and token == API_TOKEN
    peer = writer.get_extra_info("peername") if writer is not None else None
    host = peer[0] if peer else ""
    return host in ("127.0.0.1", "::1", "localhost")


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
        full = _is_full_access(params, head, writer)
        try:
            if _name == "events":
                payload = fn(params.get("since", "0"))
            else:
                payload = fn(full=full)
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
        self._server = await asyncio.start_server(
            _client_connected, self.host, self.port)
        self._watcher = asyncio.get_running_loop().create_task(_event_watcher())
        logger.info("api: dashboard JSON API on http://%s:%d/api/ (bind %s:%d)",
                    "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host,
                    self.port, self.host, self.port)
        return self

    async def __aexit__(self, *exc):
        if getattr(self, "_watcher", None) is not None:
            self._watcher.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


def serve(host=API_BIND, port=API_PORT):
    """Convenience async context manager with env-driven defaults."""
    return API(host, port)


# ---------------------------------------------------------------------------
