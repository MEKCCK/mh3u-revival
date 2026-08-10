# Unified room mesh — one server, one private network, no VPN to install

The server runs a single EasyTier mesh, and **every client that connects to the
server joins it automatically** — so everyone on the server is on one virtual
LAN, in the same gathering halls, with hunts working peer-to-peer between any
two players. No VPN app to install, no room codes, no invite flow: **the server
address IS the key**.

Built on [EasyTier](https://github.com/EasyTier/EasyTier) (the P2P mesh engine
[Terracotta](https://github.com/MEKCCK/Terracotta) embeds).

## How it works

| Part | What it is |
|---|---|
| Server node | Started automatically with the server (Host tab → Start Server). Joins a mesh whose identity is **derived from the advertised address** (`mesh_identity()`: `mh3u-<sha10(addr)>` + a baked shared secret), takes the **fixed virtual IP `10.126.126.1`**, announces itself as `mh3u-server`. |
| Client node | Started automatically on **Save + Play** (Join tab). Derives the **same** identity from the address the host gave you, joins the mesh, finds the `mh3u-server` peer, and points Cemu at its virtual IP. |
| Mesh secret | A baked public constant, BY DESIGN: the mesh is the transport plane (like a tailnet), not a security boundary — the room is invitation-only by address, and game auth is open too. |
| Public nodes | `tcp://public.easytier.top:11010` + `tcp://public2.easytier.cn:54321` handle discovery; they **relay only** when a pair can't hole-punch (symmetric NAT / CGNAT) — the mesh's built-in relay fallback. |
| Fallback | If the host's server has no mesh (runtime missing / failed / `127.0.0.1`), joiners connect directly to the advertised address, exactly like the classic flow. |

Once Cemu connects over the mesh, the server's existing mechanisms just work:
tickets point at the virtual IP, natcheck reports the virtual IPs it observes,
and session URLs restamp to the mesh plane — the server treats the mesh exactly
like it treats a Tailscale/Radmin overlay today.

## Hosting

1. Open **`MH3U_Online.exe`** → **Host** tab. Pick the IP friends will use.
2. **Start Server.** The launcher brings up the mesh first (one-time ~15 MB
   download + one admin prompt for the virtual-network driver, same as
   installing Tailscale), shows `mesh: UP — unified room at 10.126.126.1`,
   then starts the game server advertising the virtual IP.
3. Tell friends the address you picked. The dashboard below the log lists
   everyone on the mesh with their NAT type and a connection-difficulty hint.
4. **Host + Play** works the same — the launcher starts the server, then points
   your own Cemu at the mesh.

## Joining

1. Open **`MH3U_Online.exe`** → **Join** tab.
2. Enter the host's address → **Save + Play**.
3. The launcher tries the unified mesh first (downloads the runtime on first
   use, one admin prompt): if the host's server runs one, Cemu is pointed at
   the server's virtual IP and everything flows over the mesh. If not, it
   connects directly as before — no prompt, no delay.

## Notes, limits, privacy

- **Everyone on one server is on one mesh, in one set of gathering halls.**
  That's the "unified room": 16 hunters per hall by default
  (`MH3U_HALL_MAX`), 4 per hunt room (the game's own P2P limit), and any two
  players can hunt together because the mesh links them.
- **The mesh identity is derived from the address string.** Host and joiner
  must use the same string (normalized: stripped, lowercased). Different
  servers get different meshes — no cross-server traffic.
- **Fixed server IP `10.126.126.1`**: a lone easytier node never receives a
  DHCP address (assignment needs a peer), so the server pins its own; client
  nodes DHCP around it. If you change the bundled easytier and its default
  subnet changes, update `SERVER_VIRTUAL_IP` in `dist/launcher/easytier.py`.
- **Admin once.** The TUN driver needs a one-time admin install on Windows;
  the launcher relaunches itself elevated (UAC) and continues the flow
  automatically (`--join` / `--host <ip>` / `--host-play <ip>`).
- **Internet required for discovery** (public nodes). Pure-LAN players still
  connect directly through the built-in `11010` listeners.
- **Updating EasyTier:** pinned version/URLs are `MH3U_EASYTIER_VERSION` /
  `MH3U_EASYTIER_URL` (env) in `dist/launcher/easytier.py`. Pre-download:
  `python dist/launcher/fetch_easytier.py`.
- **Licensing:** EasyTier is Apache-2.0; its binaries are redistributed
  unchanged. Optional — classic Tailscale / Radmin / LAN / public-IP play
  works exactly as before.

## Troubleshooting

- **"needs admin rights" loops** — accept the UAC prompt; if you declined it,
  right-click `MH3U_Online.exe` → *Run as administrator* once.
- **Mesh up but friends can't connect** — the Host-tab dashboard shows who's
  on the mesh and their NAT type. "tough" + no connection usually means
  relaying — it should still work, give it a few seconds.
- **Join says "no mesh found … connecting directly"** — the host's server has
  no mesh (older host build, or the runtime failed to download). It still
  works if the host's address is directly reachable.
- **Why TUN and not port-forward?** MH3U hunts are Cemu-to-Cemu on dynamic
  UDP ports; a static port-forward mesh (Terracotta's `--no-tun` approach)
  cannot carry hunt traffic. The TUN interface is what makes the virtual LAN
  real for the game.
