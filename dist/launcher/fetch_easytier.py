#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# P2P component notice: see THIRD_PARTY_NOTICES.md and
# docs/HOSTED_SERVICE_ACCESS_POLICY.md.
"""Standalone downloader for the embedded EasyTier runtime.

The launcher auto-downloads the pinned easytier release into <bundle>/easytier/
on first use of the "EasyTier room" flow — you normally never need this. Run it
manually to pre-fetch (e.g. when building the bundle, or on a machine with
better connectivity), or to bump the pinned version:

    python fetch_easytier.py                 # into ./easytier/ next to this file
    python fetch_easytier.py --dir C:/x      # into a specific folder
    python fetch_easytier.py --version v2.7.0

Everything lives in easytier.py — this is just a CLI wrapper for
easytier.ensure_binaries(), so the pinned URL/logic stays in one place.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import easytier  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=_HERE,
                    help="bundle root to install easytier/ under (default: this dir)")
    ap.add_argument("--version", default=None,
                    help="easytier release tag, e.g. v2.6.4 (default: pinned)")
    args = ap.parse_args(argv)

    if args.version:
        os.environ["MH3U_EASYTIER_VERSION"] = args.version

    ok, msg = easytier.ensure_binaries(args.dir, log=print)
    print(msg or "done")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
