#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Android sparse image from a raw image, for hosts without img2simg.
Zero blocks become FILL 0, never DONT_CARE: a reflash must overwrite
the previous install, not leave it showing through. The rest is RAW.
Usage: img2simg.py RAW SPARSE [BLOCK_SIZE]"""
import os
import struct
import sys

src, dst = sys.argv[1], sys.argv[2]
bs = int(sys.argv[3]) if len(sys.argv) > 3 else 4096
size = os.path.getsize(src)
assert size % bs == 0, "image size must be a whole number of blocks"
total, zero = size // bs, bytes(bs)
with open(src, "rb") as f, open(dst, "wb") as out:
    out.write(bytes(28))
    chunks, run, kind = 0, bytearray(), None

    def flush():
        global chunks, run, kind
        if kind == "raw":
            out.write(struct.pack("<HHII", 0xCAC1, 0, len(run) // bs, 12 + len(run)))
            out.write(run)
        elif kind is not None:
            out.write(struct.pack("<HHII", 0xCAC2, 0, kind, 16))
            out.write(bytes(4))
        if kind is not None:
            chunks += 1
        run, kind = bytearray(), None

    while True:
        block = f.read(bs)
        if not block:
            break
        if block == zero:
            if kind == "raw":
                flush()
            kind = (kind or 0) + 1 if kind != "raw" else 1
        else:
            if kind not in (None, "raw"):
                flush()
            kind = "raw"
            run += block
            if len(run) >= 64 << 20:
                flush()
    flush()
    out.seek(0)
    out.write(struct.pack("<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12, bs, total, chunks, 0))
