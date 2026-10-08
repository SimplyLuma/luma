#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Create the audited FP6-private Mutter library with handheld scale limits.

This is a bring-up bridge for the exact Fedora 44 AArch64 Mutter build shipped
in the P5 image.  It never edits the system library in place.  The long-term,
source-level equivalent lives in patches/mutter/0001-luma-handheld-scales.patch.
"""

import argparse
import hashlib
import os
import shutil
import struct
import sys


EXPECTED_SHA256 = "21f8180888f0056cd5eb4193b955b50d25f037a1f4675397d3c96453e24a5a3e"
EXPECTED_BUILD_ID = "11e90c87c92684029e1844c49f930cd846b07551"

# Both sites materialize 359999 (600 * 600 - 1) before an unsigned area
# comparison.  Replace it with 143999 (240 * 600 - 1), matching the source
# patch.  The first pair is the scale enumerator; the second advertises modes.
PATCHES = {
    0x93A70: (bytes.fromhex("f8c78f52"), bytes.fromhex("f84f8652")),
    0x93A94: (bytes.fromhex("b800a072"), bytes.fromhex("5800a072")),
    0x93C2C: (bytes.fromhex("e0c78f52"), bytes.fromhex("e04f8652")),
    0x93C30: (bytes.fromhex("a000a072"), bytes.fromhex("4000a072")),
}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_elf(data):
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("input is not a 64-bit little-endian ELF")
    machine = struct.unpack_from("<H", data, 18)[0]
    if machine != 183:
        raise ValueError("input is not AArch64 ELF (EM_AARCH64)")

    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize = struct.unpack_from("<H", data, 54)[0]
    phnum = struct.unpack_from("<H", data, 56)[0]
    loads = []
    for index in range(phnum):
        offset = phoff + index * phentsize
        p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz = \
            struct.unpack_from("<IIQQQQ", data, offset)
        if p_type == 1:
            loads.append((p_vaddr, p_vaddr + p_filesz, p_offset))
    if not loads:
        raise ValueError("ELF contains no loadable segments")
    return loads


def va_to_offset(loads, virtual_address):
    for start, end, file_offset in loads:
        if start <= virtual_address < end:
            return file_offset + virtual_address - start
    raise ValueError("patch address 0x%x is not file-backed" % virtual_address)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="exact Fedora FP6 libmutter-18 library")
    parser.add_argument("output", help="new private-library path")
    args = parser.parse_args()

    actual_hash = sha256(args.input)
    if actual_hash != EXPECTED_SHA256:
        raise SystemExit(
            "refusing unknown input: expected %s, got %s" %
            (EXPECTED_SHA256, actual_hash))

    with open(args.input, "rb") as stream:
        original = stream.read()
    loads = parse_elf(original)
    patched = bytearray(original)
    changed_offsets = set()

    for address, (expected, replacement) in PATCHES.items():
        offset = va_to_offset(loads, address)
        actual = original[offset:offset + len(expected)]
        if actual != expected:
            raise SystemExit(
                "refusing unexpected code at VA 0x%x: expected %s, got %s" %
                (address, expected.hex(), actual.hex()))
        patched[offset:offset + len(expected)] = replacement
        for index, (before, after) in enumerate(zip(expected, replacement)):
            if before != after:
                changed_offsets.add(offset + index)

    observed_changes = {
        index for index, (before, after) in enumerate(zip(original, patched))
        if before != after
    }
    if observed_changes != changed_offsets or len(original) != len(patched):
        raise SystemExit("internal error: binary changes escaped audited sites")

    output_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(output_dir, exist_ok=True)
    temporary = args.output + ".tmp"
    with open(temporary, "wb") as stream:
        stream.write(patched)
        stream.flush()
        os.fsync(stream.fileno())
    shutil.copymode(args.input, temporary)
    os.replace(temporary, args.output)

    print("input_sha256=%s" % actual_hash)
    print("input_build_id=%s" % EXPECTED_BUILD_ID)
    print("changed_bytes=%d" % len(changed_offsets))
    print("output_sha256=%s" % sha256(args.output))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, struct.error) as error:
        print("error: %s" % error, file=sys.stderr)
        raise SystemExit(1)
