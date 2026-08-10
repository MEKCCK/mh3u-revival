# SPDX-License-Identifier: AGPL-3.0-only
# P2P component notice: see THIRD_PARTY_NOTICES.md and
# docs/HOSTED_SERVICE_ACCESS_POLICY.md.
"""Tests for the embedded EasyTier mesh module (dist/launcher/easytier.py).

Pure, fast, deterministic, NO network: unified-mesh identity derivation, NAT
difficulty (ported from Terracotta), peer-JSON flattening against both the
current nested easytier-cli schema and the older flat shape, and binary-
provisioning helpers.

Run:  python tests/test_easytier.py   (from the mh3u_server/ dir)
"""
import json
import os
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                      # mh3u_server/
sys.path.insert(0, os.path.join(_ROOT, "dist", "launcher"))

import easytier  # noqa: E402


def test_mesh_identity_deterministic():
    n1, s1 = easytier.mesh_identity("8.8.8.8")
    n2, s2 = easytier.mesh_identity("8.8.8.8")
    assert n1 == n2 and s1 == s2
    assert s1 == easytier.MESH_SECRET


def test_mesh_identity_normalized():
    assert easytier.mesh_identity("8.8.8.8") == easytier.mesh_identity(" 8.8.8.8 ")
    assert easytier.mesh_identity("8.8.8.8") == easytier.mesh_identity("8.8.8.8")
    assert easytier.mesh_identity("8.8.8.8") != easytier.mesh_identity("8.8.8.9")


def test_mesh_identity_shape():
    name, _ = easytier.mesh_identity("8.8.8.8")
    assert name.startswith("mh3u-")
    assert len(name) == len("mh3u-") + 10
    assert easytier.mesh_identity("")[0] == "mh3u-none"


def test_mesh_identity_joiner_server_agree():
    """Server derives from its advertised address; joiner from the typed one."""
    server = easytier.mesh_identity("203.0.113.7")
    joiner = easytier.mesh_identity("203.0.113.7")
    assert server == joiner


def test_nat_normalization():
    assert easytier.normalize_nat_type("Symmetric") == "Symmetric"
    assert easytier.normalize_nat_type("symmetric") == "Symmetric"
    assert easytier.normalize_nat_type("NoPat") == "NoPAT"
    assert easytier.normalize_nat_type("bogus") == "Unknown"
    assert easytier.normalize_nat_type(None) == "Unknown"
    assert easytier.normalize_nat_type("") == "Unknown"


def test_conn_difficulty_tiers():
    assert easytier.calc_conn_difficulty("OpenInternet", "Symmetric") == "easiest"
    assert easytier.calc_conn_difficulty("Symmetric", "OpenInternet") == "easiest"
    assert easytier.calc_conn_difficulty("FullCone", "Symmetric") == "simple"
    assert easytier.calc_conn_difficulty("NoPAT", "Tough") == "simple"
    assert easytier.calc_conn_difficulty("PortRestricted", "PortRestricted") == "medium"
    assert easytier.calc_conn_difficulty("Restricted", "Symmetric") == "medium"
    assert easytier.calc_conn_difficulty("Symmetric", "Symmetric") == "tough"
    assert easytier.calc_conn_difficulty("Unknown", "Unknown") == "tough"


def test_parse_peer_list_nested_shape():
    """Current easytier-cli shape: {"node_info": ..., "peer_routes": [{route, peer}]}."""
    raw = json.dumps({
        "node_info": {"hostname": "mh3u-host-abc", "ipv4_addr": "10.144.144.1/24",
                      "stun_info": {"udp_nat_type": "FullCone"}},
        "peer_routes": [{
            "route": {"hostname": "mh3u-player-123", "ipv4_addr": {"address": "10.144.144.2"},
                      "cost": 1},
            "peer": {"stun_info": {"udp_nat_type": "Symmetric"}},
        }],
    }).encode()
    peers = easytier.parse_peer_list(raw)
    assert len(peers) == 2
    assert peers[0]["is_self"]
    assert peers[0]["hostname"] == "mh3u-host-abc"
    assert peers[0]["ipv4"] == "10.144.144.1"
    assert peers[0]["nat_type"] == "FullCone"
    p = peers[1]
    assert p["hostname"] == "mh3u-player-123"
    assert p["ipv4"] == "10.144.144.2"
    assert p["nat_type"] == "Symmetric"
    assert not p["is_self"]


def test_parse_peer_list_flat_shape():
    """Older easytier-cli shape: a flat array with local node marked by cost."""
    raw = json.dumps([
        {"hostname": "mh3u-host-abc", "ipv4": "10.144.144.1", "cost": "Local",
         "nat_type": "FullCone"},
        {"hostname": "mh3u-player-xyz", "ipv4": "10.144.144.3", "cost": 1,
         "nat_type": "SymUdpFirewall"},
    ]).encode()
    peers = easytier.parse_peer_list(raw)
    assert len(peers) == 2
    assert peers[0]["is_self"] and peers[0]["nat_type"] == "FullCone"
    assert peers[1]["nat_type"] == "SymUdpFirewall"
    assert not peers[1]["is_self"]


def test_parse_peer_list_real_table_shape():
    """Exact sample captured from easytier-core v2.6.4 'peer list -o json'
    (flat PeerTableItem table; self is cost=Local)."""
    raw = json.dumps([
        {"cidr": "10.126.126.1/24", "ipv4": "10.126.126.1",
         "hostname": "mh3u-server", "cost": "Local", "lat_ms": "-",
         "loss_rate": "-", "rx_bytes": "-", "tx_bytes": "-",
         "tunnel_proto": "-", "nat_type": "Unknown", "id": "1872088333",
         "version": "2.6.4-84"},
    ]).encode()
    peers = easytier.parse_peer_list(raw)
    assert len(peers) == 1
    assert peers[0]["is_self"]
    assert peers[0]["hostname"] == "mh3u-server"
    assert peers[0]["ipv4"] == "10.126.126.1"
    assert peers[0]["nat_type"] == "Unknown"


def test_parse_peer_list_garbage():
    assert easytier.parse_peer_list(None) == []
    assert easytier.parse_peer_list(b"") == []
    assert easytier.parse_peer_list(b"not json") == []
    assert easytier.parse_peer_list(json.dumps({"peer_routes": "nope"}).encode()) == []
    # node_info without peer_routes -> self entry only
    only_self = json.dumps({"node_info": {"hostname": "x", "ipv4_addr": "10.0.0.1/24"}}).encode()
    peers = easytier.parse_peer_list(only_self)
    assert len(peers) == 1 and peers[0]["is_self"] and peers[0]["ipv4"] == "10.0.0.1"


def test_ipv4_normalization():
    assert easytier._ipv4_str("10.144.144.2/24") == "10.144.144.2"
    assert easytier._ipv4_str({"address": "10.144.144.2"}) == "10.144.144.2"
    assert easytier._ipv4_str("not-an-ip") == ""
    assert easytier._ipv4_str("") == ""


def test_binaries_present_missing():
    import tempfile
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        assert not easytier.binaries_present(tmp)


def test_runtime_download_preserves_upstream_license():
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        fixture = os.path.join(tmp, "release.zip")
        with zipfile.ZipFile(fixture, "w") as zf:
            for name in easytier._NEEDED:
                zf.writestr("release/" + name, b"fixture")
            zf.writestr("release/LICENSE", b"EasyTier upstream license fixture\n")

        original_download = easytier._download_with_limits
        original_mirrors = easytier.EASYTIER_MIRRORS
        try:
            def fake_download(url, dest, log, t0, deadline):
                shutil.copyfile(fixture, dest)
                return os.path.getsize(dest), 0.0

            easytier._download_with_limits = fake_download
            easytier.EASYTIER_MIRRORS = []
            ok, message = easytier.ensure_binaries(tmp, log=lambda message: None)
            assert ok, message
            license_path = os.path.join(
                easytier.easytier_dir(tmp), easytier.EASYTIER_LICENSE_FILE)
            with open(license_path, "rb") as license_file:
                assert license_file.read() == b"EasyTier upstream license fixture\n"
        finally:
            easytier._download_with_limits = original_download
            easytier.EASYTIER_MIRRORS = original_mirrors


def test_find_free_rpc_port():
    port = easytier.find_free_rpc_port(15888)
    assert 15888 <= port < 15888 + easytier.RPC_PORT_RANGE


if __name__ == "__main__":
    # tiny runner so `python tests/test_easytier.py` works without pytest
    import traceback
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("[PASS] %s" % name)
            except Exception:
                failures += 1
                print("[FAIL] %s" % name)
                traceback.print_exc()
    print("\n%s %s" % ("FAILED (%d)" % failures if failures else "ALL", "tests passed"))
    sys.exit(1 if failures else 0)
