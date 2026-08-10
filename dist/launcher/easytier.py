#!/usr/bin/env python3
"""Embedded EasyTier mesh for MH3U Revival — ONE unified room per server.

The model (from Terracotta's embedded-EasyTier pattern — github.com/MEKCCK/
Terracotta): the SERVER owns a single EasyTier network; every client that
connects to the server joins the same mesh automatically, so everyone on the
server ends up on one virtual LAN and hunts work peer-to-peer between any two
players — no Tailscale/Radmin to install, no room codes, no invite flow.

Why TUN (not Terracotta's --no-tun + port-forward): MH3U hunts are P2P between
Cemus on DYNAMIC UDP ports, so a static port-forward mesh can't carry hunt
traffic. TUN mode gives every player a virtual 10.x IP and routes everything
(like Tailscale). The mesh is the transport plane, not a security boundary:
the network secret is a baked public constant, and the network NAME is derived
deterministically from the server's advertised address (mesh_identity()), so
server and clients agree without exchanging anything.

Roles:
  * server node  — hostname "mh3u-server", DHCP-assigned virtual IP. The
                   launcher (Host tab) starts it, then launches the game
                   server with MH3U_ADVERTISE=<virtual IP>, so tickets /
                   natcheck / session URLs all point at the mesh.
  * client node  — hostname "mh3u-player-<hex>", TUN + DHCP. The launcher
                   (Join tab) starts it, finds the "mh3u-server" peer, points
                   Cemu at the server's virtual IP, and hunts run over the mesh.

Public nodes (from Terracotta's publics.rs + the EasyTier project) handle peer
discovery; they relay only when a pair can't hole-punch (symmetric NAT /
CGNAT) — the mesh's built-in "relay fallback".

Stdlib only (like the rest of the launcher) so it freezes cleanly into the
onefile exe. Pure functions (identity derivation, difficulty calc, JSON
flattening) are testable headless via `python launcher.py --selftest` /
tests/test_easytier.py.

Notes on elevation: TUN needs an admin-rights driver install on Windows on
first use (same as Tailscale). The caller decides whether to relaunch elevated;
this module just detects and reports it (is_admin()).
"""
import os
import re
import sys
import json
import time
import secrets
import hashlib
import shutil
import socket
import zipfile
import subprocess
import threading
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Identity / download / public-node configuration
# ---------------------------------------------------------------------------
# Pinned release asset we download when the bundle has no easytier/ yet.
# Override with MH3U_EASYTIER_VERSION / MH3U_EASYTIER_REPO.
EASYTIER_VERSION = os.environ.get("MH3U_EASYTIER_VERSION", "v2.6.4")
EASYTIER_REPO = os.environ.get("MH3U_EASYTIER_REPO", "EasyTier/EasyTier")
# Release asset per platform (matches EasyTier's release workflow naming).
_PLATFORM_ASSET = {
    "nt":  "easytier-windows-x86_64-%s.zip",
    "posix": "easytier-linux-x86_64-%s.zip" if sys.platform.startswith("linux")
            else "easytier-macos-x86_64-%s.zip",
}
EASYTIER_ASSET = _PLATFORM_ASSET.get(
    os.name, "easytier-linux-x86_64-%s.zip") % EASYTIER_VERSION
EASYTIER_DOWNLOAD = os.environ.get("MH3U_EASYTIER_URL") or (
    "https://github.com/%s/releases/download/%s/%s"
    % (EASYTIER_REPO, EASYTIER_VERSION, EASYTIER_ASSET))

# Public shared nodes — connect via at least one so peers can find each other
# (hole-punch first, relay fallback when a pair can't go P2P — the "relay
# fallback" from the mh3u roadmap, for free). Same list Terracotta uses.
PUBLIC_NODES = os.environ.get(
    "MH3U_EASYTIER_NODES",
    "tcp://public.easytier.top:11010,tcp://public2.easytier.cn:54321").split(",")

# LAN listeners so same-network players also connect directly (no internet
# needed for a LAN game; harmless elsewhere).
LAN_TCP_PORT = 11010
LAN_UDP_PORT = 11010

# The server node announces itself with this hostname; joiners look for it.
SERVER_HOSTNAME = "mh3u-server"
# The server node's FIXED virtual IP. A lone easytier node never gets a DHCP
# address (it needs a peer to trigger assignment), so the server pins its own
# address — deterministic, immediately available, and DHCP still serves
# clients around it (verified: server .1, clients .2+ on the default
# 10.126.126.0/24 mesh subnet). Change together with the DHCP subnet if the
# bundled easytier ever changes its default.
SERVER_VIRTUAL_IP = "10.126.126.1"
# Joiners identify themselves for the host's peer dashboard.
JOINER_HOSTNAME_PREFIX = "mh3u-player-"

# Unified-mesh network secret: baked and public, BY DESIGN. The mesh is the
# transport plane for the game's P2P traffic — like Tailscale's tailnet — not
# a security boundary (the game's auth is open too; the room itself is
# invitation-only by address). Anyone with the server's address can derive the
# identity, which is exactly what auto-joining needs.
MESH_SECRET = "mh3u-revival-mesh-v1"


def mesh_identity(seed):
    """Derive the unified mesh (network_name, network_secret) for a server.

    Both sides derive the SAME identity from the SAME string — the address
    players use to reach the server — so joining needs no code or invite: the
    server address IS the key. The secret is the shared baked constant;
    the network name is a short hash of the seed so different servers get
    different meshes (no cross-server traffic).

    `seed` must be normalized the same way on both sides (strip + lowercase);
    use the string as told to players, not a DNS-resolved IP."""
    s = str(seed or "").strip().lower()
    if not s:
        return "mh3u-none", MESH_SECRET
    return "mh3u-" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:10], MESH_SECRET


# Default RPC port easytier-cli talks to easytier-core on (scanned upward if busy).
RPC_DEFAULT_PORT = 15888
RPC_PORT_RANGE = 100


# --- NAT types easytier reports (see Terracotta's NatType + easytier proto) ---
NAT_UNKNOWN = "Unknown"
NAT_TYPES = (
    "Unknown", "OpenInternet", "NoPAT", "FullCone", "Restricted",
    "PortRestricted", "Symmetric", "SymUdpFirewall",
    "SymmetricEasyInc", "SymmetricEasyDec",
)
# easytier-cli may render "NoPat" (older) vs "NoPAT" (proto) — normalize.
_NAT_NORMALIZE = {"nopat": "NoPAT", "symudpfirewall": "SymUdpFirewall"}


def normalize_nat_type(value):
    """Map any casing/spelling of easytier's nat_type strings to one canonical
    label from NAT_TYPES; Unknown when unrecognized (fail-safe)."""
    if not value:
        return NAT_UNKNOWN
    s = str(value).strip()
    for t in NAT_TYPES:
        if s == t:
            return t
    s_low = s.lower()
    for t in NAT_TYPES:
        if s_low == t.lower():
            return t
    return _NAT_NORMALIZE.get(s_low, NAT_UNKNOWN)


def calc_conn_difficulty(left, right):
    """Port of Terracotta's calc_conn_difficulty: how hard is it to make the
    two NATs talk? Returns a tier string ('easiest'|'simple'|'medium'|'tough').
    Only used for the host's peer dashboard — never gates anything."""
    def is_one_of(types):
        return left in types or right in types
    if is_one_of(("OpenInternet",)):
        return "easiest"
    if is_one_of(("NoPAT", "FullCone")):
        return "simple"
    if is_one_of(("Restricted", "PortRestricted")):
        return "medium"
    return "tough"


def difficulty_hint(tier):
    return {
        "easiest": "open NAT — should connect directly",
        "simple": "cone NAT — direct connection expected",
        "medium": "restricted NAT — likely direct, may relay",
        "tough": "symmetric NAT — probably relaying",
    }.get(tier, "unknown")


# ---------------------------------------------------------------------------
# Binary provisioning (download on first use)
# ---------------------------------------------------------------------------
CORE_EXE = "easytier-core" + (".exe" if os.name == "nt" else "")
CLI_EXE = "easytier-cli" + (".exe" if os.name == "nt" else "")
WINTUN_DLL = "wintun.dll"
# wintun.dll ships in the Windows release (TUN driver); Linux/macOS need nothing extra.
_NEEDED = (CORE_EXE, CLI_EXE) + ((WINTUN_DLL,) if os.name == "nt" else ())

# GitHub release mirrors tried after the official URL stalls/fails (GitHub
# downloads are slow/blocked on many Chinese networks — the most common reason
# the mesh bootstrap never completes). Best-effort: a dead mirror just falls
# through. Override with MH3U_EASYTIER_MIRRORS (comma-separated); each entry
# is prefixed onto the official release URL.
_DEFAULT_MIRRORS = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://mirror.ghproxy.com/",
)
EASYTIER_MIRRORS = [m for m in (
    [s.strip() for s in os.environ.get("MH3U_EASYTIER_MIRRORS", "").split(",") if s.strip()]
    or list(_DEFAULT_MIRRORS))]


def easytier_dir(root):
    """Runtime dir for the easytier binaries (bundle_root/easytier)."""
    return os.path.join(str(root), "easytier")


def binaries_present(root):
    """True if core+cli+wintun are all sitting in the runtime dir."""
    d = easytier_dir(root)
    return all(os.path.isfile(os.path.join(d, n)) for n in _NEEDED)


# Per-attempt budgets: a stalled GitHub download (no bytes for 45s) or a total
# wall-clock cap (5 min across all mirrors) must FAIL so the launcher falls
# back to direct play instead of hanging the join flow forever.
_DL_STALL_SECS = 45.0
_DL_TOTAL_SECS = 300.0
_DL_PROGRESS_LOG_SECS = 5.0


def _download_with_limits(url, dest, log, t0, deadline):
    """Download `url` to `dest` with stall/total-deadline limits and progress
    logs. Raises on failure/timeout; returns (size_bytes, elapsed)."""
    req = urllib.request.Request(url, headers={"User-Agent": "MH3U-Revival-Launcher"})
    with urllib.request.urlopen(req, timeout=30) as r:
        total = int(r.headers.get("Content-Length", 0) or 0)
        with open(dest, "wb") as f:
            got, last_bytes, last_log, last_progress = 0, 0.0, t0, 0.0
            while True:
                if time.monotonic() - t0 > deadline:
                    raise TimeoutError("download took too long (>%.0fs)" % deadline)
                buf = r.read(1024 * 256)
                now = time.monotonic()
                if not buf:
                    break
                f.write(buf)
                got += len(buf)
                if got > last_bytes:               # got bytes: reset the stall clock
                    last_bytes, last_progress = got, now
                elif now - last_progress > _DL_STALL_SECS:
                    raise TimeoutError("download stalled (no data for %.0fs)" % _DL_STALL_SECS)
                if now - last_log > _DL_PROGRESS_LOG_SECS:
                    last_log = now
                    if total:
                        log("  ... %.1f / %.1f MB (%.0fs)"
                            % (got / 1048576.0, total / 1048576.0, now - t0))
                    else:
                        log("  ... %.1f MB (%.0fs)" % (got / 1048576.0, now - t0))
    return got, time.monotonic() - t0


def ensure_binaries(root, log=print):
    """Ensure the easytier binaries exist; download them (pinned release zip)
    into <root>/easytier/ if not. Returns (ok, message). NEVER blocks forever:
    stalled/slow downloads fail after a bounded budget and return an
    actionable error — the caller then falls back to direct play."""
    if binaries_present(root):
        return True, ""
    d = easytier_dir(root)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as e:
        return False, "cannot create %s: %s" % (d, e)

    tmp_zip = os.path.join(d, EASYTIER_ASSET + ".tmp")
    log("downloading EasyTier %s (first run only):" % EASYTIER_VERSION)
    urls = [EASYTIER_DOWNLOAD] + [m + EASYTIER_DOWNLOAD for m in EASYTIER_MIRRORS]
    t0 = time.monotonic()
    last_err = "unknown error"
    for url in urls:
        if time.monotonic() - t0 > _DL_TOTAL_SECS:
            _safe_unlink(tmp_zip)
            return False, ("failed to download EasyTier (gave up after %.0fs across %d "
                           "source(s)).\n  The bundle's auto-mesh needs the runtime once.\n"
                           "  You can still play over Tailscale/Radmin/LAN as before."
                           % (_DL_TOTAL_SECS, len(urls)))
        log("  %s" % url)
        try:
            got, elapsed = _download_with_limits(url, tmp_zip, log, t0, _DL_TOTAL_SECS)
            log("  got %.1f MB in %.1fs" % (got / 1048576.0, elapsed))
            break
        except Exception as e:
            last_err = str(e)
            _safe_unlink(tmp_zip)
            log("  ... that source failed (%s)" % last_err)
    else:
        _safe_unlink(tmp_zip)
        return False, ("failed to download EasyTier (%s; tried %d source(s) for %.0fs).\n"
                       "  The bundle's auto-mesh needs the runtime once.\n"
                       "  You can still play over Tailscale/Radmin/LAN as before."
                       % (last_err, len(urls), time.monotonic() - t0))
    try:
        with zipfile.ZipFile(tmp_zip) as zf:
            names = {Path(n).name: n for n in zf.namelist() if not n.endswith("/")}
            for n in _NEEDED:
                member = names.get(n) or names.get(n.lower())
                if member is None:
                    raise KeyError(n)
                with zf.open(member) as src, open(os.path.join(d, n), "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except Exception as e:
        _safe_unlink(tmp_zip)
        for n in _NEEDED:
            _safe_unlink(os.path.join(d, n))
        return False, "unpacking EasyTier failed (%s) — try again later" % e
    _safe_unlink(tmp_zip)
    if not binaries_present(root):
        return False, "EasyTier release zip did not contain the expected files"
    # Unix: make the binaries executable.
    if os.name != "nt":
        for n in (CORE_EXE, CLI_EXE):
            p = os.path.join(d, n)
            try:
                os.chmod(p, os.stat(p).st_mode | 0o100)
            except OSError:
                pass
    log("EasyTier ready in %s" % d)
    return True, ""


def _safe_unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Process / admin helpers
# ---------------------------------------------------------------------------

def _no_window_flags():
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def is_admin():
    """Windows: are we running elevated? (TUN driver install needs it once.)
    Non-Windows: True (posix TUN via /dev/net/tun needs no privilege here)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # noqa
    except Exception:
        return False


def relaunch_elevated(argv_extra=None):
    """Relaunch this exe as admin (UAC prompt). Returns True if we spawned the
    elevated copy. Caller should exit afterwards."""
    if os.name != "nt":
        return False
    extra = list(argv_extra or [])
    try:
        import ctypes
        if getattr(sys, "frozen", False):
            exe, args = sys.executable, extra + sys.argv[1:]
        else:
            exe, args = sys.executable, [sys.argv[0]] + extra + sys.argv[1:]
        ctypes.windll.shell32.ShellExecuteW(  # noqa
            None, "runas", exe, subprocess.list2cmdline(args), None, 1)
        return True
    except Exception:
        return False


def find_free_rpc_port(preferred):
    """Pick a free TCP port for easytier's RPC, starting at `preferred`."""
    for port in range(preferred, preferred + RPC_PORT_RANGE):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            pass
        finally:
            s.close()
    return 0


def _kill_tree(proc):
    """Kill a process and its whole child tree (easytier-core may spawn
    helper processes on Windows). Mirrors the launcher's server kill logic."""
    if proc is None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                creationflags=_no_window_flags(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Peer-list JSON parsing (easytier-cli -o json peer list)
# ---------------------------------------------------------------------------
# The CLI output schema varies across easytier versions. Current shape:
#   {"node_info": {..., "hostname": ..., "ipv4_addr": "10.x.x.x/24", ...},
#    "peer_routes": [{"route": {..., "hostname", "ipv4_addr", "cost", ...},
#                     "peer":  {"stun_info": {"udp_nat_type": ...}, ...}}]}
# Terracotta's parser reads a flatter shape. We flatten defensively so one
# parser works for any easytier we might ship.

def _first(d, *keys):
    """First non-empty value found walking nested dicts for any of `keys`."""
    if isinstance(d, dict):
        for k in keys:
            v = d.get(k)
            if v is not None and v != "":
                return v
        for v in d.values():
            r = _first(v, *keys)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _first(v, *keys)
            if r is not None:
                return r
    return None


def _ipv4_str(value):
    """Normalize an ipv4 field that may be '10.1.2.3', '10.1.2.3/24', or a
    {'address': ...} object — returns the bare dotted quad or ''."""
    if isinstance(value, dict):
        value = value.get("address") or value.get("addr") or ""
    if not value:
        return ""
    s = str(value).split("/")[0].strip()
    try:
        socket.inet_aton(s)
        return s
    except OSError:
        return ""


def parse_peer_list(raw_json):
    """Parse easytier-cli 'peer list -o json' output into a list of peer dicts:
    {hostname, ipv4, cost, nat_type, is_self}. Tolerant of schema drift;
    never raises. `raw_json` may be bytes or str."""
    if not raw_json:
        return []
    if isinstance(raw_json, bytes):
        try:
            raw_json = raw_json.decode("utf-8", "replace")
        except Exception:
            return []
    try:
        data = json.loads(raw_json)
    except (ValueError, TypeError):
        return []

    out = []
    self_entry = None
    flat = isinstance(data, list)
    if flat:
        items = data
    elif isinstance(data, dict):
        # Current easytier-cli shape: self lives in node_info, others in
        # peer_routes. Normalize the self node into a regular peer entry so
        # callers (peer dashboard, difficulty vs host NAT) can rely on is_self.
        node_info = data.get("node_info")
        if isinstance(node_info, dict):
            self_entry = {
                "hostname": str(node_info.get("hostname") or ""),
                "ipv4": _ipv4_str(_first(node_info, "ipv4_addr", "ipv4", "addr")),
                "cost": "Local",
                "nat_type": normalize_nat_type(
                    _first(node_info, "nat_type", "udp_nat_type")),
                "is_self": True,
            }
        routes = data.get("peer_routes")
        items = routes if isinstance(routes, list) else []
    else:
        items = []

    if self_entry is not None:
        out.append(self_entry)

    for item in items:
        if not isinstance(item, dict):
            continue
        ip = _ipv4_str(_first(item, "ipv4", "ipv4_addr", "addr"))
        hostname = _first(item, "hostname")
        cost = _first(item, "cost")
        nat_raw = _first(item, "nat_type", "udp_nat_type")
        if isinstance(nat_raw, dict):  # {"udp_nat_type": "Symmetric"}
            nat_raw = _first(nat_raw, "udp_nat_type", "n")
        if ip or hostname:
            is_self = False
            if flat:
                # Older easytier-cli marked the local node in the list itself.
                is_self = str(cost).lower() in ("local", "self") if cost else False
            out.append({
                "hostname": str(hostname) if hostname else "",
                "ipv4": ip,
                "cost": str(cost) if cost else "",
                "nat_type": normalize_nat_type(nat_raw),
                "is_self": is_self,
            })
    return out


# ---------------------------------------------------------------------------
# EasyTierNet — one running easytier-core instance
# ---------------------------------------------------------------------------

class EasyTierNet:
    """Wraps an easytier-core process on the server's unified mesh.

    Usage:  net = EasyTierNet(root); ok, msg = net.start(name, secret, hostname)
    then net.self_ip() / net.server_ip() / net.wait_for_server(...) /
    net.list_peers() / net.stop().
    """

    def __init__(self, root, log=print, rpc_port=None):
        self.root = str(root)
        self.dir = easytier_dir(self.root)
        self.core = os.path.join(self.dir, CORE_EXE)
        self.cli = os.path.join(self.dir, CLI_EXE)
        self.rpc_port = rpc_port or find_free_rpc_port(RPC_DEFAULT_PORT)
        self.log = log
        self._proc = None
        self._reader = None
        self._started_name = None

    # -- lifecycle ----------------------------------------------------------
    def start(self, network_name, network_secret, hostname, ipv4=None,
              extra_peers=None):
        """Spawn easytier-core (TUN mode) joined to the mesh identified by
        (name, secret). `ipv4` pins our virtual address (the server node uses
        this — DHCP alone never assigns an IP to a lone node, so the server
        must be deterministic). `extra_peers` adds direct peer URLs (e.g. the
        server's own listener, tcp://<server-ip>:11010) — best-effort,
        unreachable peers are harmless. Returns (ok, message)."""
        if not binaries_present(self.root):
            return False, "easytier binaries missing — run ensure_binaries() first"
        if self._proc is not None and self._proc.poll() is None:
            return False, "easytier already running (rpc=%d)" % self.rpc_port
        if self.rpc_port == 0:
            return False, "no free RPC port found"

        args = [self.core,
                "-r", "127.0.0.1:%d" % self.rpc_port,
                "--network-name", network_name,
                "--network-secret", network_secret,
                "--hostname", hostname]
        if ipv4:
            args += ["--ipv4", ipv4]
        # -d: let the mesh DHCP-assign each client node's virtual 10.x.x.x
        # address (same flag Terracotta passes; without it no IP is assigned
        # at all). The server node pins its own instead.
        args += ["-d"]
        for node in PUBLIC_NODES:
            node = node.strip()
            if node:
                args += ["-p", node]
        for peer in (extra_peers or []):
            peer = peer.strip()
            if peer:
                args += ["-p", peer]
        args += ["-l", "tcp://0.0.0.0:%d" % LAN_TCP_PORT,
                 "-l", "udp://0.0.0.0:%d" % LAN_UDP_PORT]
        self.log("easytier command: %s" % " ".join(args))

        self.log("starting EasyTier mesh (rpc=%d, hostname=%s) ..."
                 % (self.rpc_port, hostname))
        try:
            proc = subprocess.Popen(
                args, cwd=self.dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
                creationflags=_no_window_flags())
        except OSError as e:
            return False, "failed to start easytier-core: %s" % e
        self._proc = proc
        self._started_name = hostname
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        return True, "easytier started"

    def _pump(self):
        try:
            for line in iter(self._proc.stdout.readline, ""):
                if line.strip():
                    self.log("[easytier] %s" % line.rstrip("\n"))
        except Exception:
            pass

    def stop(self):
        """Kill easytier-core (whole tree). Safe to call repeatedly."""
        proc, self._proc = self._proc, None
        if proc is not None:
            self.log("[easytier] stopping ...")
            _kill_tree(proc)

    def is_alive(self):
        return self._proc is not None and self._proc.poll() is None

    # -- easytier-cli RPC ---------------------------------------------------
    def _cli(self, *args):
        """Run easytier-cli against our RPC port; return stdout or ''."""
        if not self.is_alive():
            return ""
        try:
            out = subprocess.run(
                [self.cli, "-p", "127.0.0.1:%d" % self.rpc_port] + list(args),
                cwd=self.dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=8,
                creationflags=_no_window_flags())
            return out.stdout or ""
        except Exception:
            return ""

    def self_ip(self):
        """Our own virtual IP on the mesh ('10.x.x.x') or ''. easytier-cli's
        'peer list -o json' returns a flat table (self marked cost=Local);
        some versions nest node_info/peer_routes instead — the parser handles
        both."""
        for peer in parse_peer_list(self._cli("-o", "json", "peer", "list")):
            if peer["is_self"] and peer["ipv4"]:
                return peer["ipv4"]
        return ""

    def list_peers(self):
        """All nodes on the network (including self), as parse_peer_list dicts."""
        return parse_peer_list(self._cli("-o", "json", "peer", "list"))

    def server_ip(self):
        """Virtual IP of the mesh's server node ('mh3u-server') — '' until it
        appears. Works from either side: the server sees its own node."""
        for peer in self.list_peers():
            if peer["hostname"] == SERVER_HOSTNAME and peer["ipv4"]:
                return peer["ipv4"]
        return ""

    def is_server(self):
        return self._started_name == SERVER_HOSTNAME

    def wait_for_server(self, timeout=90, interval=1.0, log=None):
        """Poll until the server node's virtual IP appears (or we ARE the
        server and our own IP is assigned). Returns the IP, or None on
        timeout. `log`, if given, is called with progress strings."""
        waited = 0.0
        while self.is_alive() and waited < timeout:
            ip = self.self_ip() if self.is_server() else self.server_ip()
            if ip:
                return ip
            waited += interval
            if log and waited % 5 < interval:
                log("  mesh connecting ... %.0fs" % waited)
            time.sleep(interval)
        return None

    def peer_nat(self, ip):
        """nat_type of the peer at `ip` (or Unknown)."""
        for peer in self.list_peers():
            if peer["ipv4"] == ip:
                return peer["nat_type"]
        return NAT_UNKNOWN
