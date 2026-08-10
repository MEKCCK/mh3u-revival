# Dashboard JSON API

The server exposes a tiny HTTP JSON API that feeds the webui dashboard panel:
live players, hunt rooms, gathering halls, occupancy caps, and recent log
lines. No third-party deps — it runs inside the server's event loop.

## Quick start

```
MH3U_API_BIND=0.0.0.0 python server.py     # default bind is 127.0.0.1
```

Then open `http://<server-ip>:1623/api/` — it lists the endpoints.

| Env | Default | Meaning |
|---|---|---|
| `MH3U_API` | `1` | `0` disables the API server entirely |
| `MH3U_API_PORT` | `1623` | listen port |
| `MH3U_API_BIND` | `127.0.0.1` | bind address. Set `0.0.0.0` for a panel on another machine — note this exposes player IPs and room data to the LAN |
| `MH3U_API_LOG` | `on` | `off` disables the log ring buffer |

CORS is open (`Access-Control-Allow-Origin: *`, OPTIONS preflight handled), so
a browser panel anywhere can call it directly.

## Endpoints

All responses are JSON; every snapshot is fail-open (partial data + `"error"`
key instead of a 500 when the game-state module is unavailable).

### `GET /api/status`
Server identity, topology, advertised address (the mesh virtual IP when the
unified-room mesh is on), uptime, and abuse/DoS caps:

```json
{
  "server": "mh3u-revival",
  "game_server_id": "0x10104d00",
  "ports": {"auth": 1223, "secure": 1224, "natcheck": 10025, "api": 1623},
  "advertised_address": "10.126.126.1",
  "advertise_override": null,
  "uptime_s": 1234.5,
  "caps": {"connections": 128, "per_ip": 16, "rooms": 48, "room_participants": 32},
  "halls": {"hall_max": 16, "room_max": 4, "num_worlds": 1}
}
```

### `GET /api/players`
Live secure connections — the core "who is online" view:

```json
{
  "count": 3,
  "players": [
    {"pid": 1234, "name": "Hunter1234", "ip": "10.126.126.2",
     "plane": "private", "cid": 5, "uptime_s": 312.1, "idle_s": 1.2,
     "rooms": ["0x1001"], "halls": ["0x101"]}
  ]
}
```

* `plane` — reachability plane the player arrived on: `loopback`,
  `overlay (Tailscale)`, `overlay (Radmin)`, `private`, `link-local`,
  `public`, `unknown`.
* `rooms` / `halls` — hex gathering ids the player is currently in.

### `GET /api/rooms`
Hunt rooms (MatchmakeSessions):

```json
{"count": 1, "rooms": [
  {"gid": "0x1001", "host_pid": 1234, "host_name": "Hunter1234",
   "num_participants": 2, "max_participants": 4, "game_mode": 0,
   "attribs": [0, 0, 0, 0, 0, 0, 0, 0, 0], "application_data_len": 309,
   "participants": [{"pid": 1234, "name": "Hunter1234"}]}
]}
```

### `GET /api/halls`
Gathering halls + lobbies (communities), including the pre-seeded official
worlds (`official: true`, `is_lobby: false`):

```json
{"count": 2, "halls": [
  {"gid": "0x101", "name": "Gathering Hall 1", "owner_pid": 2,
   "official": true, "is_lobby": false, "num_participants": 3,
   "displayed_population": 5, "max_participants": 18,
   "participants": [{"pid": 1234, "name": "Hunter1234"}]}
]}
```

### `GET /api/stats`
Occupancy vs caps, per-IP connection counts, rate-limit state, reaper config:

```json
{
  "uptime_s": 1234.5,
  "connections": {"current": 3, "max": 128, "per_ip_max": 16},
  "rooms": {"current": 1, "max": 48},
  "halls": {"current": 2, "runtime": 0, "max": 64},
  "by_ip": [{"ip": "10.126.126.2", "connections": 1}],
  "shout_tracked_pids": 0,
  "reaper": {"timeout_s": 45.0, "interval_s": 15.0}
}
```

### `GET /api/log?tail=N`
The last N formatted log lines (cap 500, default 200):

```json
{"lines": ["16:04:22 INFO  mh3u.server: MH3U NEX server ...", "..."]}
```

### `GET /api/`
Endpoint index.

## Notes

* The API handler never blocks or crashes a game handler — it is one coroutine
  on the event loop, and every snapshot is defensive.
* Polling cadence: `/api/players` + `/api/rooms` + `/api/halls` every 2-3s is
  plenty; `/api/log` only on demand.
* Auth is open and the data is not sensitive (private friends server), but the
  API does expose player IPs — keep `MH3U_API_BIND` at `127.0.0.1` unless the
  panel needs remote access.
