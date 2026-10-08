#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Inspect the uncompressed Tokay boot-kernel entry contract.

Pixel 9 stock boot payloads use the Linux arm64 Image header together with a
PE32+ EFI-application header.  This parser is deliberately small and
dependency-free so offline builders can fail closed before wrapping a payload
in an Android boot image.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import struct
import sys


def unpack_from(fmt: str, data: bytes, offset: int) -> tuple[int, ...] | None:
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        return None
    return struct.unpack_from(fmt, data, offset)


def is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=pathlib.Path)
    parser.add_argument("--require-stock-contract", action="store_true")
    args = parser.parse_args()

    image = args.image.read_bytes()
    failures: list[str] = []

    arm64_magic = len(image) >= 60 and image[56:60] == b"ARMd"
    if not arm64_magic:
        failures.append("arm64-image-magic")
    declared_tuple = unpack_from("<Q", image, 16)
    declared_size = declared_tuple[0] if declared_tuple else 0
    if declared_size and declared_size < len(image):
        failures.append("arm64-declared-size")

    dos_signature = len(image) >= 2 and image[:2] == b"MZ"
    if not dos_signature:
        failures.append("mz-signature")
    pe_offset_tuple = unpack_from("<I", image, 0x3C)
    pe_offset = pe_offset_tuple[0] if pe_offset_tuple else 0
    pe_signature = (
        pe_offset >= 0x40
        and pe_offset + 24 <= len(image)
        and image[pe_offset : pe_offset + 4] == b"PE\0\0"
    )
    if not pe_signature:
        failures.append("pe-signature")

    machine = section_count = optional_size = optional_magic = 0
    entry_point = section_alignment = file_alignment = 0
    size_of_image = size_of_headers = subsystem = 0
    entry_in_section = False
    sections_valid = False

    if pe_signature:
        coff = unpack_from("<HHIIIHH", image, pe_offset + 4)
        assert coff is not None
        machine, section_count, _, _, _, optional_size, _ = coff
        if machine != 0xAA64:
            failures.append("pe-machine")
        if section_count == 0:
            failures.append("pe-sections-empty")

        optional_offset = pe_offset + 24
        optional = unpack_from("<H", image, optional_offset)
        optional_magic = optional[0] if optional else 0
        if optional_magic != 0x20B:
            failures.append("pe32plus-magic")
        if optional_size < 70 or optional_offset + optional_size > len(image):
            failures.append("pe-optional-header-size")
        else:
            entry_point = struct.unpack_from("<I", image, optional_offset + 16)[0]
            section_alignment = struct.unpack_from("<I", image, optional_offset + 32)[0]
            file_alignment = struct.unpack_from("<I", image, optional_offset + 36)[0]
            size_of_image = struct.unpack_from("<I", image, optional_offset + 56)[0]
            size_of_headers = struct.unpack_from("<I", image, optional_offset + 60)[0]
            subsystem = struct.unpack_from("<H", image, optional_offset + 68)[0]

            if entry_point == 0 or entry_point >= size_of_image:
                failures.append("pe-entry-point")
            if not is_power_of_two(section_alignment):
                failures.append("pe-section-alignment")
            if not is_power_of_two(file_alignment):
                failures.append("pe-file-alignment")
            if size_of_headers == 0 or size_of_headers > len(image):
                failures.append("pe-size-of-headers")
            if subsystem != 10:
                failures.append("pe-subsystem")

            section_table = optional_offset + optional_size
            sections_valid = section_table + section_count * 40 <= len(image)
            if sections_valid:
                for index in range(section_count):
                    offset = section_table + index * 40
                    virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
                        "<IIII", image, offset + 8
                    )
                    extent = max(virtual_size, raw_size)
                    if extent and virtual_address <= entry_point < virtual_address + extent:
                        entry_in_section = True
                    if raw_size and raw_pointer + raw_size > len(image):
                        sections_valid = False
            if not sections_valid:
                failures.append("pe-section-table")
            elif not entry_in_section:
                failures.append("pe-entry-section")

    contract_matches = not failures
    values = {
        "LUMA_PIXEL9_KERNEL_ENTRY_INSPECTION_VERSION": "1",
        "IMAGE_BYTES": str(len(image)),
        "IMAGE_SHA256": hashlib.sha256(image).hexdigest(),
        "ARM64_IMAGE_MAGIC_PRESENT": str(arm64_magic).lower(),
        "ARM64_IMAGE_DECLARED_BYTES": str(declared_size),
        "DOS_MZ_SIGNATURE_PRESENT": str(dos_signature).lower(),
        "PE_COFF_HEADER_PRESENT": str(pe_signature).lower(),
        "PE_OFFSET": f"0x{pe_offset:x}",
        "PE_MACHINE": f"0x{machine:x}",
        "PE_SECTION_COUNT": str(section_count),
        "PE_OPTIONAL_HEADER_BYTES": str(optional_size),
        "PE_OPTIONAL_MAGIC": f"0x{optional_magic:x}",
        "PE_ENTRY_POINT": f"0x{entry_point:x}",
        "PE_SECTION_ALIGNMENT": f"0x{section_alignment:x}",
        "PE_FILE_ALIGNMENT": f"0x{file_alignment:x}",
        "PE_SIZE_OF_IMAGE": f"0x{size_of_image:x}",
        "PE_SIZE_OF_HEADERS": f"0x{size_of_headers:x}",
        "PE_SUBSYSTEM": str(subsystem),
        "PE_SUBSYSTEM_NAME": "EFI_APPLICATION" if subsystem == 10 else "other",
        "PE_SECTIONS_STRUCTURALLY_VALID": str(sections_valid).lower(),
        "PE_ENTRY_IN_SECTION": str(entry_in_section).lower(),
        "ENTRY_CONTRACT_KIND": "linux-arm64-pe32plus-efi-application",
        "ENTRY_CONTRACT_MATCHES_STOCK": str(contract_matches).lower(),
        "ENTRY_CONTRACT_FAILURES": ",".join(failures) if failures else "none",
        "PHONE_ACCESSED": "false",
        "BOOT_AUTHORIZED": "false",
        "FLASH_AUTHORIZED": "false",
    }
    for key, value in values.items():
        print(f"{key}={value}")

    if args.require_stock_contract and not contract_matches:
        print(
            "error: payload does not match the stock Tokay ARM64 PE/COFF EFI entry contract",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
