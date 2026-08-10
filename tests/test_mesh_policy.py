# SPDX-License-Identifier: AGPL-3.0-only
# P2P component notice: see THIRD_PARTY_NOTICES.md and
# docs/HOSTED_SERVICE_ACCESS_POLICY.md.
"""Pure checks for the server-side unified-mesh address policy."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "..", "external", "NintendoClients"))

import config  # noqa: E402
import matchmaking_handlers  # noqa: E402


class _Client:
    def __init__(self, address):
        self.address = address
        self.pid = 100

    def remote_address(self):
        return (self.address, 1224)


def test_mesh_ipv4():
    assert config.is_mesh_address("10.126.126.1")
    assert config.is_mesh_address("10.126.126.254")


def test_mesh_ipv4_mapped():
    assert config.is_mesh_address("::ffff:10.126.126.3")


def test_non_mesh_addresses():
    assert not config.is_mesh_address("36.62.189.214")
    assert not config.is_mesh_address("127.0.0.1")
    assert not config.is_mesh_address("")
    assert not config.is_mesh_address(None)


def test_room_policy_allows_mesh_and_rejects_direct():
    previous = config.REQUIRE_MESH
    config.REQUIRE_MESH = True
    try:
        matchmaking_handlers._require_mesh_client(_Client("10.126.126.3"), "join")
        try:
            matchmaking_handlers._require_mesh_client(_Client("36.62.189.214"), "join")
        except Exception as exc:
            assert "SessionVoid" in str(exc)
        else:
            raise AssertionError("direct client was accepted while mesh was required")
    finally:
        config.REQUIRE_MESH = previous


def test_room_policy_is_compatible_when_disabled():
    previous = config.REQUIRE_MESH
    config.REQUIRE_MESH = False
    try:
        matchmaking_handlers._require_mesh_client(_Client("36.62.189.214"), "join")
    finally:
        config.REQUIRE_MESH = previous


if __name__ == "__main__":
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
    print("\n%s" % ("FAILED (%d)" % failures if failures else "ALL tests passed"))
    sys.exit(1 if failures else 0)
