#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the tester's virtual Desktop EDID (1080p60, sRGB, aspect-only size).

LMA is a local virtual-device identifier, not a registered hardware vendor ID.
The disposable guest supplies its matching hwdb name. No physical monitor
identity or physical-device profile is changed.
"""
from pathlib import Path
import struct
import sys


def desktop_edid():
    block = bytearray(128)
    block[:8] = bytes.fromhex('00 ff ff ff ff ff ff 00')
    vendor = (12 << 10) | (13 << 5) | 1  # LMA
    struct.pack_into('>H', block, 8, vendor)
    struct.pack_into('<HI', block, 10, 1, 1)
    block[16:25] = bytes([1, 36, 1, 4, 0x80, 16, 9, 120, 0x06])
    # sRGB chromaticity coordinates, in EDID's 10-bit fixed-point representation.
    coords = [655, 338, 307, 614, 154, 61, 320, 337]
    block[25] = sum((coords[i] & 3) << (6 - 2 * i) for i in range(4))
    block[26] = sum((coords[i + 4] & 3) << (6 - 2 * i) for i in range(4))
    block[27:35] = bytes(value >> 2 for value in coords)
    block[35:38] = bytes.fromhex('21 08 00')  # VGA/SVGA/XGA compatibility modes
    block[38:54] = bytes.fromhex('d1 c0 a9 c0 81 c0 81 80 01 01 01 01 01 01 01 01')
    # CTA 1920x1080p60 timing, 148.5MHz, positive sync. Aspect-only 160x90mm
    # avoids claiming a physical diagonal or triggering automatic HiDPI scaling.
    block[54:72] = bytes.fromhex('02 3a 80 18 71 38 2d 40 58 2c 45 00 a0 5a 00 00 00 1e')
    def text(tag, value):
        return bytes([0, 0, 0, tag, 0]) + (value + '\n').ljust(13).encode('ascii')
    block[72:90] = text(0xfc, 'Desktop')
    block[90:108] = text(0xff, 'Luma Tester')
    block[108:126] = bytes.fromhex('00 00 00 fd 00 32 4b 1e 55 11 00 0a 20 20 20 20 20 20')
    block[127] = -sum(block) & 255
    return block


if __name__ == '__main__':
    Path(sys.argv[1]).write_bytes(desktop_edid())
