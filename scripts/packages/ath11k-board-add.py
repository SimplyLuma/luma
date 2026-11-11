#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Append one board entry to an ath11k board-2.bin.

    ath11k-board-add.py IN OUT NAME DATA

NAME is the board name ath11k asks for (for example
bus=ahb,qmi-chip-id=1,qmi-board-id=255,variant=Fairphone_Gen_6) and DATA the
board data file (bdwlan.elf).
"""
import struct
import sys

MAGIC = b"QCA-ATH11K-BOARD\0"
IE_BOARD, IE_BOARD_NAME, IE_BOARD_DATA = 0, 0, 1


def element(kind, payload):
    return struct.pack("<II", kind, len(payload)) + payload + b"\0" * (-len(payload) % 4)


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    source, output, name, data = sys.argv[1:]
    with open(source, "rb") as handle:
        blob = handle.read()
    if not blob.startswith(MAGIC) or len(blob) % 4:
        sys.exit(f"error: {source} is not an ath11k board-2.bin")
    with open(data, "rb") as handle:
        board = handle.read()
    entry = element(IE_BOARD, element(IE_BOARD_NAME, name.encode()) + element(IE_BOARD_DATA, board))
    with open(output, "wb") as handle:
        handle.write(blob + entry)


if __name__ == "__main__":
    main()
