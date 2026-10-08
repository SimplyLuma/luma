#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Convert a binary PPM (as `virsh screenshot` writes) to PNG, stdlib only."""

import struct
import sys
import zlib


def read_token(data, offset):
    while data[offset:offset + 1].isspace() or data[offset:offset + 1] == b"#":
        if data[offset:offset + 1] == b"#":
            offset = data.index(b"\n", offset)
        offset += 1
    end = offset
    while not data[end:end + 1].isspace():
        end += 1
    return data[offset:end], end


def convert(source, target):
    data = open(source, "rb").read()
    magic, offset = read_token(data, 0)
    if magic != b"P6":
        raise SystemExit(f"error: {source} is not a binary PPM")
    width, offset = read_token(data, offset)
    height, offset = read_token(data, offset)
    maxval, offset = read_token(data, offset)
    width, height = int(width), int(height)
    if int(maxval) != 255:
        raise SystemExit("error: only 8-bit PPM is supported")
    pixels = data[offset + 1:offset + 1 + width * height * 3]
    rows = b"".join(b"\x00" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    open(target, "wb").write(png)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
