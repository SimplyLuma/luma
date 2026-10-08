#!/usr/bin/env python3
"""Inspect a Qualcomm ParameterParser camera profile without loading its HAL.

The tool intentionally emits only structural metadata and register operations.
It does not copy tuning payloads into the Luma source tree.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import asdict, dataclass
from pathlib import Path


HEADER_MAGIC = b"QTI Chromatix Header"
SECTION_TABLE_OFFSET = 0xA0
SECTION_RECORD_SIZE = 12
SYMBOL_RECORD_SIZE = 16
REGISTER_RECORD_SIZE = 56
REGISTER_SYMBOL_OFFSETS = (4, 12, 20, 32, 52)
REGISTER_ADDRESS_OFFSET = 24
REGISTER_VALUE_SYMBOL_OFFSET = 32
REGISTER_OPERATION_OFFSET = 44
REGISTER_DELAY_SYMBOL_OFFSET = 52


@dataclass(frozen=True)
class Section:
    offset: int
    size: int
    kind: int


@dataclass(frozen=True)
class Symbol:
    identifier: int
    data_offset: int
    size: int
    reference: int


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def symbol_u32(
    data: bytes,
    data_section: Section,
    symbols: list[Symbol],
    identifier: int,
    inline_offset: int,
) -> int:
    if not 1 <= identifier <= len(symbols):
        raise ValueError(f"invalid symbol identifier: {identifier}")
    symbol = symbols[identifier - 1]
    if symbol.identifier != identifier:
        raise ValueError(f"non-contiguous symbol table at identifier {identifier}")
    if symbol.size == 0:
        return u32(data, inline_offset)
    if symbol.size != 4:
        raise ValueError(
            f"symbol {identifier} has unsupported scalar size {symbol.size}"
        )
    value_offset = data_section.offset + symbol.data_offset
    if value_offset + 4 > data_section.offset + data_section.size:
        raise ValueError(f"symbol {identifier} scalar exceeds the data section")
    return u32(data, value_offset)


def parse_sections(data: bytes) -> list[Section]:
    if not data.startswith(HEADER_MAGIC):
        raise ValueError("not a QTI Chromatix ParameterParser container")

    count = u32(data, 0x98)
    if not 1 <= count <= 32:
        raise ValueError(f"implausible section count: {count}")

    end = SECTION_TABLE_OFFSET + count * SECTION_RECORD_SIZE
    if end > len(data):
        raise ValueError("truncated section table")

    sections = []
    for index in range(count):
        offset = SECTION_TABLE_OFFSET + index * SECTION_RECORD_SIZE
        section = Section(*struct.unpack_from("<III", data, offset))
        if section.offset + section.size > len(data):
            raise ValueError(f"section {index} exceeds the container")
        sections.append(section)
    return sections


def parse_symbols(data: bytes, section: Section) -> list[Symbol]:
    if section.size % SYMBOL_RECORD_SIZE:
        raise ValueError("symbol-table size is not record aligned")
    return [
        Symbol(*struct.unpack_from("<IIII", data, offset))
        for offset in range(
            section.offset,
            section.offset + section.size,
            SYMBOL_RECORD_SIZE,
        )
    ]


def is_register_array(
    data: bytes,
    data_section: Section,
    symbol: Symbol,
    symbols: list[Symbol],
) -> bool:
    if symbol.size < REGISTER_RECORD_SIZE or symbol.size % REGISTER_RECORD_SIZE:
        return False
    start = data_section.offset + symbol.data_offset
    end = start + symbol.size
    if start < data_section.offset or end > data_section.offset + data_section.size:
        return False

    record_count = symbol.size // REGISTER_RECORD_SIZE
    symbol_count = len(symbols)
    expected_identifier = symbol.identifier + 1
    for record_index in range(record_count):
        record = start + record_index * REGISTER_RECORD_SIZE
        for field_offset in REGISTER_SYMBOL_OFFSETS:
            identifier = u32(data, record + field_offset)
            if identifier != expected_identifier or identifier > symbol_count:
                return False
            expected_identifier += 1

        address_identifier = u32(data, record + 20)
        value_identifier = u32(data, record + REGISTER_VALUE_SYMBOL_OFFSET)
        try:
            address = symbol_u32(
                data,
                data_section,
                symbols,
                address_identifier,
                record + REGISTER_ADDRESS_OFFSET,
            )
            value = symbol_u32(
                data,
                data_section,
                symbols,
                value_identifier,
                record + 36,
            )
        except ValueError:
            return False
        if address > 0xFFFF or value > 0xFFFF:
            return False
    return True


def register_arrays(
    data: bytes,
    data_section: Section,
    symbols: list[Symbol],
) -> list[dict[str, object]]:
    result = []
    for symbol in symbols:
        if not is_register_array(data, data_section, symbol, symbols):
            continue
        start = data_section.offset + symbol.data_offset
        registers = []
        for index in range(symbol.size // REGISTER_RECORD_SIZE):
            record = start + index * REGISTER_RECORD_SIZE
            address_identifier = u32(data, record + 20)
            value_identifier = u32(data, record + REGISTER_VALUE_SYMBOL_OFFSET)
            delay_identifier = u32(data, record + REGISTER_DELAY_SYMBOL_OFFSET)
            delay_symbol = symbols[delay_identifier - 1]
            if delay_symbol.size == 0:
                delay_us = 0
            else:
                delay_us = symbol_u32(
                    data,
                    data_section,
                    symbols,
                    delay_identifier,
                    record + REGISTER_DELAY_SYMBOL_OFFSET,
                )
            registers.append(
                {
                    "address": symbol_u32(
                        data,
                        data_section,
                        symbols,
                        address_identifier,
                        record + REGISTER_ADDRESS_OFFSET,
                    ),
                    "value": symbol_u32(
                        data,
                        data_section,
                        symbols,
                        value_identifier,
                        record + 36,
                    ),
                    "operation": u32(data, record + REGISTER_OPERATION_OFFSET),
                    "delay_us": delay_us,
                }
            )
        result.append(
            {
                "symbol_id": symbol.identifier,
                "data_offset": symbol.data_offset,
                "record_count": len(registers),
                "registers": registers,
            }
        )
    return result


def discover_awb_gain_tables(data: bytes) -> list[dict[str, object]]:
    """Find Qualcomm's ten RGB illuminant-gain triplets.

    The profiles store ten consecutive ``[R, 1.0, B]`` float triplets from
    cool to warm.  Real tables have meaningful variation, descend overall in
    R, ascend overall in B, and retain the ordered end segments.  Those
    constraints reject the large constant-one tables elsewhere in Chromatix.
    """

    one = struct.pack("<f", 1.0)
    position = data.find(one)
    matches = []
    seen = set()
    while position >= 0:
        start = position - 4
        if (
            start >= 0
            and start not in seen
            and start + 120 <= len(data)
            and all(
                data[start + 4 + 12 * index : start + 8 + 12 * index] == one
                for index in range(10)
            )
        ):
            values = struct.unpack_from("<30f", data, start)
            red = values[0::3]
            blue = values[2::3]
            plausible = all(
                math.isfinite(value) and 0.15 < value < 10.0
                for value in (*red, *blue)
            )
            red_variation = max(red) - min(red)
            blue_variation = max(blue) - min(blue)
            if (
                plausible
                and red_variation > 0.3
                and blue_variation > 0.3
                and red[0] > red[-1]
                and blue[0] < blue[-1]
                and sum(red[index] > red[index + 1] for index in range(3)) >= 2
                and sum(blue[index] < blue[index + 1] for index in range(3)) >= 2
                and red[-3] > red[-2] > red[-1]
                and blue[-3] < blue[-2] < blue[-1]
            ):
                seen.add(start)
                matches.append(
                    {
                        "offset": start,
                        "triplets": [
                            [values[index], values[index + 1], values[index + 2]]
                            for index in range(0, 30, 3)
                        ],
                        # Indices 4-6 are the fluorescent, intentionally
                        # off-locus samples in the FP6 profiles.
                        "awb_locus_gains_warm_to_cool": [
                            [red[index], blue[index]]
                            for index in (9, 8, 7, 3, 2, 1, 0)
                        ],
                    }
                )
        position = data.find(one, position + 1)
    return matches


def _valid_ccm(matrix: tuple[float, ...]) -> bool:
    if len(matrix) != 9 or not all(math.isfinite(value) for value in matrix):
        return False
    if max(abs(value) for value in matrix) > 4.0:
        return False
    if any(abs(sum(matrix[index : index + 3]) - 1.0) > 0.002 for index in (0, 3, 6)):
        return False
    # Exclude identity/default matrices and degenerate numeric coincidences.
    identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    if max(abs(value - expected) for value, expected in zip(matrix, identity)) < 0.05:
        return False
    determinant = (
        matrix[0] * (matrix[4] * matrix[8] - matrix[5] * matrix[7])
        - matrix[1] * (matrix[3] * matrix[8] - matrix[5] * matrix[6])
        + matrix[2] * (matrix[3] * matrix[7] - matrix[4] * matrix[6])
    )
    return abs(determinant) >= 0.05


def discover_ccm_families(data: bytes) -> list[dict[str, object]]:
    """Find contiguous CCM-plus-zero-offset families in Chromatix data."""

    zero_offsets = b"\0" * 12
    starts = []
    position = data.find(zero_offsets)
    while position >= 0:
        start = position - 36
        if (
            start >= 0
            and start + 144 <= len(data)
            and data[start + 84 : start + 96] == zero_offsets
            and data[start + 132 : start + 144] == zero_offsets
        ):
            matrices = [
                struct.unpack_from("<9f", data, start + stride)
                for stride in (0, 48, 96)
            ]
            if all(_valid_ccm(matrix) for matrix in matrices):
                starts.append(start)
        position = data.find(zero_offsets, position + 1)

    families = []
    index = 0
    while index < len(starts):
        chain = [starts[index]]
        index += 1
        while index < len(starts) and starts[index] == chain[-1] + 48:
            chain.append(starts[index])
            index += 1
        matrices = [
            struct.unpack_from("<9f", data, chain[0] + stride)
            for stride in range(0, 48 * (len(chain) + 2), 48)
        ]
        families.append(
            {
                "offset": chain[0],
                "matrix_count": len(matrices),
                "matrices": [list(matrix) for matrix in matrices],
            }
        )
    return families


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", type=Path)
    parser.add_argument(
        "--include-registers",
        action="store_true",
        help="include decoded address/value pairs in JSON output",
    )
    parser.add_argument(
        "--preview-registers",
        type=int,
        default=0,
        metavar="COUNT",
        help="include the first and last COUNT decoded pairs for each array",
    )
    parser.add_argument(
        "--emit-c-array",
        type=int,
        metavar="SYMBOL_ID",
        help="emit one decoded register array as CCI_REG16 initializer rows",
    )
    parser.add_argument(
        "--discover-color-calibration",
        action="store_true",
        help="discover ten-illuminant AWB gains and row-normalized CCM families",
    )
    args = parser.parse_args()

    data = args.profile.read_bytes()
    sections = parse_sections(data)
    symbol_sections = [section for section in sections if section.kind == 2]
    data_sections = [section for section in sections if section.kind == 1]
    if len(symbol_sections) != 1 or not data_sections:
        raise ValueError("unexpected ParameterParser section layout")

    # Symbol offsets are relative to the main (largest) type-1 data section.
    data_section = max(data_sections, key=lambda section: section.size)
    symbols = parse_symbols(data, symbol_sections[0])
    arrays = register_arrays(data, data_section, symbols)
    if args.emit_c_array is not None:
        matches = [
            item for item in arrays if item["symbol_id"] == args.emit_c_array
        ]
        if len(matches) != 1:
            parser.error(
                f"symbol {args.emit_c_array} is not a decoded register array"
            )
        print(
            "\n".join(
                f"\t{{ CCI_REG16(0x{item['address']:04x}), "
                f"0x{item['value']:04x} }},"
                + (f" /* delay {item['delay_us']} us */" if item["delay_us"] else "")
                for item in matches[0]["registers"]
            )
        )
        return 0
    if args.preview_registers < 0:
        parser.error("--preview-registers must be non-negative")
    if not args.include_registers and args.preview_registers:
        preview_count = args.preview_registers
        arrays = [
            {
                key: value
                for key, value in item.items()
                if key != "registers"
            }
            | {
                "registers_first": item["registers"][:preview_count],
                "registers_last": item["registers"][-preview_count:],
            }
            for item in arrays
        ]
    elif not args.include_registers:
        arrays = [
            {key: value for key, value in item.items() if key != "registers"}
            for item in arrays
        ]

    output = {
        "profile": args.profile.name,
        "container_size": len(data),
        "sections": [asdict(section) for section in sections],
        "symbol_count": len(symbols),
        "register_arrays": arrays,
    }
    if args.discover_color_calibration:
        output["awb_gain_tables"] = discover_awb_gain_tables(data)
        output["ccm_families"] = discover_ccm_families(data)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
