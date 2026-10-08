#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render /usr/lib/os-release for a Luma OS build (ADR-030 section 3, ADR-040).

The file names the release the way people see it, "Luma (Prairie, Beta 0,
Nightly 20260916)", from the release identity in config/os/release.env and the
build's channel and nightly date (scripts/os/lib/release_identity.py). Fedora
compatibility fields that tools read to find the platform (ID_LIKE,
PLATFORM_ID, the Fedora release) are kept.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_identity  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=release_identity.CONTRACT)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--nightly-date", default="")
    parser.add_argument("--build-date", required=True)
    args = parser.parse_args()
    try:
        identity = release_identity.load(args.contract)
        sys.stdout.write(release_identity.render_os_release(
            identity, args.channel, args.build_id, args.nightly_date, args.build_date))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
