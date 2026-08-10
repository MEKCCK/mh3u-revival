# SPDX-License-Identifier: AGPL-3.0-only
# P2P component notice: see THIRD_PARTY_NOTICES.md and
# docs/HOSTED_SERVICE_ACCESS_POLICY.md.
"""NAT-check responder — tells each client its OWN address as this server sees it.

Why this exists (the rooms-of-2 bug, 2026-07-05): the game discovers its public
station by asking Nintendo's NAT-check service (nncs*.app.nintendowifi.net, still
alive) and then relays that station PEER-TO-PEER through the room host to set up
joiner<->joiner mesh links. That exchange never touches the NEX server, so the
RMC-side probe restamp can't fix it: on an overlay VPN that doesn't own the
default route (Radmin), every client self-publishes its raw ISP-NAT station, the
mesh candidates are on the wrong plane, and every joiner<->joiner link fails —
rooms cap at host+1. Host links survive only because the host's station comes
from get_session_urls / the restamped probes (server-built, right plane).

Fix: the patched Cemu resolves the nncs* hostnames to THIS server instead
(nsysnet DNS override reading mh3u_server.txt), so the game's NAT discovery runs
against us over whatever plane it uses to reach us — and we answer with the
source address+port we observe. The reply comes from the GAME socket's own
flow, so the reported port is the game socket's mapping (better than the RMC
restamp, which could only guess from the PRUDP socket). For a pure-public /
port-forward client the observed address equals what Nintendo would have said,
so behavior there is unchanged.

Wire format — reverse-engineered 2026-07-05 by replaying the GAME's captured
requests against Nintendo's still-live nncs1/nncs2.app.nintendowifi.net:10025
(scratchpad/probe_nncs.py). MH3U does NOT use Kinnay's type==1 shape; it sends:
  request  = >IIII (type, 0, 0, 0)      type cycles 0x65, 0x66, 0x67
  response = >IIII (type, observed_port, observed_ipv4_u32, server_ipv4_u32)
Real servers answer 0x65 and 0x67 (echoing the type) and DELIBERATELY DROP 0x66
(both real servers, consistently) — so we mirror that: reply to 0x65/0x67 only,
never 0x66. Verified against a live capture: the reply's word1 was the exact
source port the probe left from and word2 was the prober's observed public IPv4,
i.e. the server reports the source endpoint it observed — exactly what we do.
Anything else is logged (hexdumped) and NOT answered, so we don't act as a
general UDP reflector and any future format change surfaces in the log.
"""
import asyncio
import logging
import socket
import struct
import time

import config
import limits

logger = logging.getLogger("mh3u.natcheck")

NATCHECK_PORT = limits._int_env("MH3U_NATCHECK_PORT", 10025)
NATCHECK_ENABLED = limits._bool_env("MH3U_NATCHECK", True)

# Request types the real nncs servers answer (echoing the type). 0x66 is sent by
# the game but intentionally dropped by the real servers, so we drop it too.
_REPLY_TYPES = (0x65, 0x67)

# --- per-client NAT-behavior observation (vnt-style, observation only) -------
# vnt classifies a NAT as symmetric when probes from DIFFERENT local sockets
# come back with DIFFERENT public endpoints (see vnt/src/nat/stun.rs). We can't
# probe the client, but we see the inverse signal for free: when a client keeps
# showing up from NEW random source ports, it's the same port-remapping NAT
# signature. That's a warning sign for P2P hole-punching (the game's peers may
# end up relayed), so we log a one-time hint per client. Never gates anything.
_MAX_OBSERVED_IPS = 256          # bound the tracking table (abuse-safe)
_MAX_PORTS_PER_IP = 8            # distinct ports remembered per client
_HINT_AFTER_PORTS = 2            # distinct source ports before hinting

_seen = {}      # ip -> (set of source ports, last-seen monotonic)
_hinted = set() # ips we already warned about (once per session)


def _observe_endpoint(host, port):
    """Record a probe source endpoint; return (hint_text_or_None, n_ports).
    Fail-open: an internal error returns (None, 0) and never breaks the
    responder."""
    try:
        now = time.monotonic()
        entry = _seen.get(host)
        if entry is None:
            if len(_seen) >= _MAX_OBSERVED_IPS:
                return None, 0
            ports = set()
            entry = (ports, now)
        else:
            ports, _last = entry
        if port not in ports:
            if len(ports) >= _MAX_PORTS_PER_IP:
                ports.clear()
            ports.add(port)
        _seen[host] = (ports, now)
        if len(ports) >= _HINT_AFTER_PORTS and host not in _hinted:
            return ("NAT hint: %s keeps probing from new source ports (%s) — "
                    "port-remapping (symmetric-ish) NAT signature; its P2P "
                    "links may fall back to relaying"
                    % (host, ",".join(str(p) for p in sorted(ports)))), len(ports)
        return None, len(ports)
    except Exception:
        return None, 0


def _maybe_hint(host, port):
    """Observe a probe and log the one-time NAT hint (WARNING so hosts see it)."""
    hint, _n = _observe_endpoint(host, port)
    if hint:
        _hinted.add(host)
        logger.warning("natcheck: %s", hint)


def _ip_u32(dotted):
    return struct.unpack(">I", socket.inet_aton(dotted))[0]


class _NatCheckProtocol(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        host, port = addr[0], addr[1]
        if len(data) == 16:
            try:
                type_, _a, _b, _c = struct.unpack(">IIII", data)
            except struct.error:
                type_ = None
            if type_ == 0x66:
                _maybe_hint(host, port)
                return   # real servers silently drop 0x66; mirror that
            if type_ in _REPLY_TYPES:
                _maybe_hint(host, port)
                # Co-located host player: their bundle points at 127.0.0.1, so the
                # observed source is loopback — useless to a remote peer. Substitute
                # ADVERTISE (same rule as protocols._build_public). The port is still
                # right (loopback = no NAT = the game socket's real local port).
                report = host
                if config.ADVERTISE_ADDRESS and (host.startswith("127.") or host == "::1"):
                    report = config.ADVERTISE_ADDRESS
                # word3 in the real reply is the responding server's own address; the
                # game uses word1(port)+word2(ip) as its discovered public endpoint.
                server_ip = config.ADVERTISE_ADDRESS or report
                resp = struct.pack(">IIII", type_, port, _ip_u32(report), _ip_u32(server_ip))
                self.transport.sendto(resp, addr)
                logger.info("natcheck: type=0x%x %s:%d -> reported endpoint %s:%d",
                            type_, host, port, report, port)
                return
        logger.warning("natcheck: unrecognized %d-byte packet from %s:%d: %s",
                       len(data), host, port, data[:64].hex())

    def error_received(self, exc):
        logger.info("natcheck: transport error: %s", exc)


async def start(host):
    """Bind the responder; returns the transport (or None when disabled/bind fails).
    Fail-open: NAT-check is an assist for overlay-VPN mesh links, not a hard
    dependency — the server must come up even if the port is taken."""
    if not NATCHECK_ENABLED:
        logger.info("natcheck: disabled (MH3U_NATCHECK=0)")
        return None
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            _NatCheckProtocol, local_addr=(host, NATCHECK_PORT))
    except OSError as e:
        logger.warning("natcheck: could not bind %s:%d (%s); NAT-check redirect inactive",
                       host, NATCHECK_PORT, e)
        return None
    logger.info("natcheck: reporting observed addresses on %s:%d", host, NATCHECK_PORT)
    return transport
