# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the application-store prototype tests."""
from __future__ import annotations

import struct
from pathlib import Path


def write_elf(path: Path, soname: str = "", needed: tuple[str, ...] = (), versions: tuple[str, ...] = (),
              symbols: tuple[str, ...] = ()) -> Path:
    """A minimal little-endian x86_64 shared object: dynamic section, exported
    symbols and symbol version definitions, and no code."""
    strings = b"\0"
    offsets: dict[str, int] = {}
    for name in (soname, *needed, *versions, *symbols):
        if name and name not in offsets:
            offsets[name] = len(strings)
            strings += name.encode() + b"\0"
    header_size, entry = 64, 56
    dynamic_offset = header_size + 2 * entry
    entries = [(1, offsets[name]) for name in needed]
    if soname:
        entries.append((14, offsets[soname]))
    strtab_offset = dynamic_offset + 16 * (len(entries) + 2)
    entries += [(5, strtab_offset), (0, 0)]
    dynamic = b"".join(struct.pack("<qQ", tag, value) for tag, value in entries)
    dynsym = struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0) + b"".join(
        struct.pack("<IBBHQQ", offsets[name], 0x12, 0, 1, 0x1000, 0) for name in symbols)
    verdef = b""
    for index, name in enumerate(versions):
        last = index == len(versions) - 1
        verdef += struct.pack("<HHHHIII", 1, 0, index + 2, 1, 0, 20, 0 if last else 28) + struct.pack("<II", offsets[name], 0)
    dynsym_offset = strtab_offset + len(strings)
    verdef_offset = dynsym_offset + len(dynsym)
    section_offset = verdef_offset + len(verdef)
    sections = [(0, 0, 0, 0, 0), (3, strtab_offset, len(strings), 0, 0), (11, dynsym_offset, len(dynsym), 1, 24)]
    if verdef:
        sections.append((0x6FFFFFFD, verdef_offset, len(verdef), 1, 0))
    table = b"".join(struct.pack("<IIQQQQIIQQ", 0, kind, 0, 0, offset, size, link, 0, 1, entsize)
                     for kind, offset, size, link, entsize in sections)
    header = (b"\x7fELF" + bytes((2, 1, 1, 0)) + b"\0" * 8
              + struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, header_size, section_offset, 0, header_size, entry, 2, 64,
                            len(sections), 0))
    total = section_offset + len(table)
    load = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, total, total, 0x1000)
    dyn = struct.pack("<IIQQQQQQ", 2, 6, dynamic_offset, dynamic_offset, dynamic_offset, len(dynamic), len(dynamic), 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + load + dyn + dynamic + strings + dynsym + verdef + table)
    return path


def write_elf32(path: Path, soname: str = "", needed: tuple[str, ...] = ()) -> Path:
    """A minimal little-endian i386 shared object: a dynamic section and no code."""
    strings = b"\0"
    offsets: dict[str, int] = {}
    for name in (soname, *needed):
        if name and name not in offsets:
            offsets[name] = len(strings)
            strings += name.encode() + b"\0"
    header_size, entry = 52, 32
    dynamic_offset = header_size + 2 * entry
    entries = [(1, offsets[name]) for name in needed]
    if soname:
        entries.append((14, offsets[soname]))
    strtab_offset = dynamic_offset + 8 * (len(entries) + 2)
    entries += [(5, strtab_offset), (0, 0)]
    dynamic = b"".join(struct.pack("<iI", tag, value) for tag, value in entries)
    total = strtab_offset + len(strings)
    header = (b"\x7fELF" + bytes((1, 1, 1, 0)) + b"\0" * 8
              + struct.pack("<HHIIIIIHHHHHH", 3, 3, 1, 0, header_size, 0, 0, header_size, entry, 2, 40, 0, 0))
    load = struct.pack("<IIIIIIII", 1, 0, 0, 0, total, total, 5, 0x1000)
    dyn = struct.pack("<IIIIIIII", 2, dynamic_offset, dynamic_offset, dynamic_offset, len(dynamic), len(dynamic), 6, 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + load + dyn + dynamic + strings)
    return path
