#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Decode the legacy 32 KiB Qualcomm QSEE diagnostic ring.

The input is the exact root-only sysfs snapshot produced by Luma's bounded
QSEE-log kernel. This tool handles only the downstream legacy header
(`u16 wrap`, `u16 offset`) and emits printable diagnostic text; it does not
parse or retain biometric command buffers.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


QSEE_LOG_SIZE = 0x8000
HEADER_SIZE = 4


def decode_ring(blob: bytes) -> tuple[int, int, bytes]:
    if len(blob) != QSEE_LOG_SIZE:
        raise ValueError(
            f"expected exactly {QSEE_LOG_SIZE} bytes, received {len(blob)}"
        )

    wrap, offset = struct.unpack_from("<HH", blob)
    ring = blob[HEADER_SIZE:]
    if offset > len(ring):
        raise ValueError(f"ring offset {offset} exceeds payload size {len(ring)}")

    if wrap:
        ordered = ring[offset:] + ring[:offset]
    else:
        ordered = ring[:offset]

    return wrap, offset, ordered


def printable_text(payload: bytes) -> str:
    chars: list[str] = []
    for byte in payload:
        if byte in (0x09, 0x0A, 0x0D) or 0x20 <= byte <= 0x7E:
            chars.append(chr(byte))
        elif byte == 0:
            chars.append("\n")
        else:
            chars.append(".")
    return "".join(chars).strip("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()

    try:
        wrap, offset, payload = decode_ring(args.snapshot.read_bytes())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(f"qsee_log_wrap={wrap} qsee_log_offset={offset}")
    text = printable_text(payload)
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
