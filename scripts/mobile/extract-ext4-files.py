#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read files from an ext4 image without mounting or modifying it.

This intentionally supports only extent-backed regular files/directories.  It
fails closed on unsupported inode layouts instead of trying to repair or mount
an image.
"""

from __future__ import annotations

import argparse
import os
import stat
import struct
from pathlib import Path


class Ext4Image:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.file = path.open("rb")
        self.file.seek(1024)
        sb = self.file.read(1024)
        if len(sb) != 1024 or struct.unpack_from("<H", sb, 0x38)[0] != 0xEF53:
            raise ValueError("not an ext filesystem image")
        self.block_size = 1024 << struct.unpack_from("<I", sb, 0x18)[0]
        self.first_data_block = struct.unpack_from("<I", sb, 0x14)[0]
        self.blocks_per_group = struct.unpack_from("<I", sb, 0x20)[0]
        self.inodes_per_group = struct.unpack_from("<I", sb, 0x28)[0]
        self.inode_size = struct.unpack_from("<H", sb, 0x58)[0]
        self.desc_size = max(32, struct.unpack_from("<H", sb, 0xFE)[0])
        self.gdt_offset = (self.first_data_block + 1) * self.block_size

    def close(self) -> None:
        self.file.close()

    def _read_at(self, offset: int, size: int) -> bytes:
        self.file.seek(offset)
        data = self.file.read(size)
        if len(data) != size:
            raise ValueError("short read from filesystem image")
        return data

    def inode(self, number: int) -> bytes:
        if number < 1:
            raise ValueError("invalid inode number")
        group, index = divmod(number - 1, self.inodes_per_group)
        gd = self._read_at(self.gdt_offset + group * self.desc_size, self.desc_size)
        table = struct.unpack_from("<I", gd, 0x08)[0]
        if self.desc_size >= 64:
            table |= struct.unpack_from("<I", gd, 0x28)[0] << 32
        return self._read_at(table * self.block_size + index * self.inode_size,
                             self.inode_size)

    @staticmethod
    def inode_size_bytes(inode: bytes) -> int:
        return struct.unpack_from("<I", inode, 0x04)[0] | (
            struct.unpack_from("<I", inode, 0x6C)[0] << 32
        )

    def _extent_blocks(self, node: bytes) -> list[tuple[int, int, int]]:
        magic, entries, _maximum, depth, _generation = struct.unpack_from(
            "<HHHHI", node, 0
        )
        if magic != 0xF30A:
            raise ValueError("inode is not extent-backed")
        result: list[tuple[int, int, int]] = []
        if depth == 0:
            for offset in range(12, 12 + entries * 12, 12):
                logical, length_raw, start_hi, start_lo = struct.unpack_from(
                    "<IHHI", node, offset
                )
                length = length_raw & 0x7FFF
                if length == 0:
                    length = 32768
                physical = start_lo | (start_hi << 32)
                result.append((logical, length, physical))
            return result
        for offset in range(12, 12 + entries * 12, 12):
            _logical, leaf_lo, leaf_hi, _unused = struct.unpack_from(
                "<IIHH", node, offset
            )
            child = self._read_at((leaf_lo | (leaf_hi << 32)) * self.block_size,
                                  self.block_size)
            result.extend(self._extent_blocks(child))
        return result

    def read_inode(self, number: int) -> tuple[int, bytes]:
        inode = self.inode(number)
        mode = struct.unpack_from("<H", inode, 0)[0]
        flags = struct.unpack_from("<I", inode, 0x20)[0]
        if not flags & 0x80000:
            raise ValueError(f"inode {number} does not use extents")
        size = self.inode_size_bytes(inode)
        output = bytearray(size)
        for logical, length, physical in self._extent_blocks(inode[0x28:0x64]):
            file_offset = logical * self.block_size
            if file_offset >= size:
                continue
            count = min(length * self.block_size, size - file_offset)
            output[file_offset:file_offset + count] = self._read_at(
                physical * self.block_size, count
            )
        return mode, bytes(output)

    def directory(self, number: int) -> dict[str, int]:
        mode, data = self.read_inode(number)
        if not stat.S_ISDIR(mode):
            raise ValueError(f"inode {number} is not a directory")
        entries: dict[str, int] = {}
        offset = 0
        while offset + 8 <= len(data):
            inode, record_len, name_len, _kind = struct.unpack_from(
                "<IHBB", data, offset
            )
            if record_len < 8 or offset + record_len > len(data):
                offset = ((offset // self.block_size) + 1) * self.block_size
                continue
            if inode and name_len <= record_len - 8:
                raw_name = data[offset + 8:offset + 8 + name_len]
                name = raw_name.decode("utf-8", "surrogateescape")
                if name not in (".", ".."):
                    entries[name] = inode
            offset += record_len
        return entries

    def resolve(self, path: str) -> int:
        inode = 2
        for component in (part for part in path.split("/") if part):
            entries = self.directory(inode)
            if component not in entries:
                raise FileNotFoundError(path)
            inode = entries[component]
        return inode

    def walk_matches(self, names: set[str]) -> list[tuple[str, int]]:
        matches: list[tuple[str, int]] = []
        pending = [("", 2)]
        seen = {2}
        while pending:
            parent, inode_number = pending.pop()
            for name, child in self.directory(inode_number).items():
                path = f"{parent}/{name}"
                mode = struct.unpack_from("<H", self.inode(child), 0)[0]
                if name in names and stat.S_ISREG(mode):
                    matches.append((path, child))
                elif stat.S_ISDIR(mode) and child not in seen:
                    seen.add(child)
                    pending.append((path, child))
        return sorted(matches)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("names", nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    image = Ext4Image(args.image)
    try:
        matches = image.walk_matches(set(args.names))
        if not matches:
            raise SystemExit("no requested files found")
        for source, inode_number in matches:
            mode, data = image.read_inode(inode_number)
            if not stat.S_ISREG(mode):
                raise ValueError(f"not a regular file: {source}")
            destination = args.output / os.path.basename(source)
            if destination.exists():
                raise FileExistsError(destination)
            destination.write_bytes(data)
            os.chmod(destination, 0o600)
            print(f"{source}\t{len(data)}\t{destination}")
    finally:
        image.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
