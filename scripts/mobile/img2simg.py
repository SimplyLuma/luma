#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Android sparse image from a raw image, for hosts without img2simg.

Zero blocks become FILL 0, never DONT_CARE: a reflash must overwrite the
previous install, not leave it showing through. The rest is RAW.

With --at OFFSET the raw image is placed OFFSET bytes into the partition and
everything before it is DONT_CARE: flashing it writes that range only (the
device kit's LumaBoot filesystem inside userdata).

Usage: img2simg.py RAW SPARSE [BLOCK_SIZE] [--at OFFSET]"""
import argparse
import os
import struct

RAW, FILL, DONT_CARE = 0xCAC1, 0xCAC2, 0xCAC3


def convert(src: str, dst: str, bs: int = 4096, at: int = 0) -> None:
    size = os.path.getsize(src)
    if size % bs or at % bs:
        raise SystemExit("img2simg: image size and offset must be whole blocks")
    zero = bytes(bs)
    with open(src, "rb") as f, open(dst, "wb") as out:
        out.write(bytes(28))
        chunks = 0
        run, kind = bytearray(), None  # kind: None, "raw" or a count of zero blocks

        def flush():
            nonlocal chunks, run, kind
            if kind == "raw":
                out.write(struct.pack("<HHII", RAW, 0, len(run) // bs, 12 + len(run)))
                out.write(run)
            elif kind is not None:
                out.write(struct.pack("<HHII", FILL, 0, kind, 16))
                out.write(bytes(4))
            if kind is not None:
                chunks += 1
            run, kind = bytearray(), None

        if at:
            out.write(struct.pack("<HHII", DONT_CARE, 0, at // bs, 12))
            chunks += 1
        while True:
            block = f.read(bs)
            if not block:
                break
            if block == zero:
                if kind == "raw":
                    flush()
                kind = (kind or 0) + 1
            else:
                if kind not in (None, "raw"):
                    flush()
                kind = "raw"
                run += block
                if len(run) >= 64 << 20:
                    flush()
        flush()
        out.seek(0)
        out.write(struct.pack("<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12, bs, (at + size) // bs, chunks, 0))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("raw")
    parser.add_argument("sparse")
    parser.add_argument("block_size", nargs="?", type=int, default=4096)
    parser.add_argument("--at", type=int, default=0, help="byte offset of RAW in the partition")
    args = parser.parse_args()
    convert(args.raw, args.sparse, args.block_size, args.at)


if __name__ == "__main__":
    main()
