#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Print a static delta's directory below <repo>/deltas/ (OSTree layout).

  delta_path.py FROM TO     FROM may be empty for a from-empty delta

OSTree names a delta by the modified base64 (no padding, '/' -> '_') of the
binary checksums: <2 chars>/<rest>[-<to>] for from-empty, and
<from 2 chars>/<from rest>-<to> otherwise.
"""

import base64
import sys


def mb64(checksum: str) -> str:
    return base64.b64encode(bytes.fromhex(checksum)).decode().rstrip("=").replace("/", "_")


def path(source: str, target: str) -> str:
    to = mb64(target)
    if not source:
        return f"{to[:2]}/{to[2:]}"
    frm = mb64(source)
    return f"{frm[:2]}/{frm[2:]}-{to}"


if __name__ == "__main__":
    print(path(sys.argv[1], sys.argv[2]))
