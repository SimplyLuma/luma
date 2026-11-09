#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write Android sparse images onto a raw image the way fastboot flashes them.

RAW and FILL chunks are written, DONT_CARE chunks leave the target as it is.
`simg-apply.py userdata.raw generic.sparse kit.sparse` gives the disk a phone
has after `fastboot flash userdata` of both files: the off-device tests boot
it, and a host can flash the result as one image when a bootloader erases
userdata before every flash.

Usage: simg-apply.py TARGET SPARSE... [--create SIZE]"""
import argparse
import os
import struct


def apply(target, sparse_path: str, fresh: bool = False) -> None:
    with open(sparse_path, "rb") as f:
        magic, major, _minor, header, chunk_header, bs, total, count, _crc = struct.unpack("<IHHHHIIII", f.read(28))
        if magic != 0xED26FF3A or major != 1:
            raise SystemExit(f"simg-apply: {sparse_path} is not an Android sparse image")
        f.seek(header)
        block = 0
        for _ in range(count):
            kind, _reserved, blocks, size = struct.unpack("<HHII", f.read(12))
            f.seek(chunk_header - 12, os.SEEK_CUR)
            body = size - chunk_header
            if kind == 0xCAC1:
                target.seek(block * bs)
                left = blocks * bs
                while left:
                    data = f.read(min(left, 64 << 20))
                    target.write(data)
                    left -= len(data)
            elif kind == 0xCAC2:
                value = f.read(4)
                if not (fresh and value == bytes(4)):  # a new file already reads as zeros
                    target.seek(block * bs)
                    chunk = value * (bs // 4)
                    for _ in range(blocks):
                        target.write(chunk)
            elif kind == 0xCAC3:
                f.seek(body, os.SEEK_CUR)
            elif kind == 0xCAC4:
                f.seek(body, os.SEEK_CUR)
                continue
            else:
                raise SystemExit(f"simg-apply: unknown chunk type {kind:#x}")
            block += blocks
        if block != total:
            raise SystemExit(f"simg-apply: {sparse_path} covers {block} blocks, header says {total}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target")
    parser.add_argument("sparse", nargs="+")
    parser.add_argument("--create", metavar="SIZE", type=int, help="start from a new zero file of SIZE bytes")
    args = parser.parse_args()
    fresh = args.create is not None
    with open(args.target, "w+b" if fresh else "r+b") as target:
        if fresh:
            target.truncate(args.create)
        for index, path in enumerate(args.sparse):
            apply(target, path, fresh=fresh and index == 0)


if __name__ == "__main__":
    main()
