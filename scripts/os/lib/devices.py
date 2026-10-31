#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Phone hardware targets from devices.tsv (shared with devices.sh).

  devices.py targets   print every admitted hardware target, one per line
"""

import os
import sys

TABLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devices.tsv")
FIELDS = ("hardware_target", "slug", "arch", "import", "base_kind", "required_labels", "image_id")


def load(path=TABLE):
    devices = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if not line.strip() or line.startswith("#"):
                continue
            row = line.rstrip("\n").split("\t")
            if len(row) != len(FIELDS):
                raise ValueError("malformed device table")
            devices[row[0]] = dict(zip(FIELDS, row))
    return devices


def device(target, path=TABLE):
    """Return the table row for an explicit target; refuse anything unlisted."""
    try:
        return load(path)[target]
    except KeyError:
        raise ValueError("unsupported hardware target") from None


def ref(target, major, channel):
    row = device(target)
    return f"luma/{major}/{row['arch']}/{row['slug']}/{channel}"


def namespace(target):
    row = device(target)
    return f"targets/{row['arch']}/{row['slug']}"


if __name__ == "__main__":
    if sys.argv[1:] == ["targets"]:
        print("\n".join(load()))
        sys.exit(0)
    print(__doc__, file=sys.stderr)
    sys.exit(2)
