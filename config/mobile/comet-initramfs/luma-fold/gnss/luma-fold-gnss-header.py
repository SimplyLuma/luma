#!/usr/bin/python3
"""Apply the Fold's gps.cfg chip policy to kepler.bin's header block.

  luma-fold-gnss-header.py <kepler.bin in> <kepler.bin out>

Stock gnssd rewrites header fields from /vendor/etc/gnss/gps.cfg before the
second (header) stage. The field offsets are the ones exynos-modem's
gnss-codeload names for the Pixel 9a header (912 bytes); the Fold's 960-byte
header has the same layout up to 0x38f plus a 48-byte tail, except a one-byte
shift between 0x256 and 0x28f, so the DCXO, customer and product-model bytes
there are left alone. Every field is checked against the raw value seen in the
Fold's stock image first; anything unexpected leaves the image untouched.
Values: comet gps.cfg (RfMiscCtrl, AidingConfiguration, mems_data_Configuration,
GNSSConstConstraintDef, scheduling_enabled, SDL_BYASS, ANDRD_MEAS_CORR_ENABLE,
VDR_*, Timemark_Option); ACT and the watchdog as stock writes them on the 9a.
"""
import struct
import sys

HDR_LEN = 0x3C0
# offset: (size, raw value in the Fold's stock header, value to write)
FIELDS = {
    0x00C: (4, 0x00000000, 0x80008001),  # Chip_Configuration_RfMiscCtrl
    0x018: (4, 0x000200CF, 0x00020046),  # Chip_Configuration_AidingConfiguration
    0x01C: (4, 0x00000007, 0x00000025),  # Chip_Configuration_mems_data_Configuration
    0x028: (1, 0x00, 0x05),              # ACT
    0x029: (1, 0x00, 0x05),              # ACT-L5
    0x02C: (4, 0x00000832, 0x00000C33),  # FeatureCfg: SDL bypass (bit 0), AMC (bit 10)
    0x20E: (2, 0x00FF, 0x60FF),          # GNSSConstConstraintDef
    0x23C: (4, 0x00000000, 0x00000001),  # scheduling_enabled (bit 0)
    0x330: (2, 0x0708, 0x0000),          # watchdog period
    0x340: (4, 300, 200),                # VDR high-end distance
    0x344: (4, 300, 200),                # VDR low-end distance
    0x348: (4, 60, 20),                  # VDR high-end time
    0x34C: (4, 60, 20),                  # VDR low-end time
    0x38C: (1, 0x00, 0x01),              # ChipCfg_Timemark_Option
}
FMT = {1: "<B", 2: "<H", 4: "<I"}


def main():
    src, dst = sys.argv[1], sys.argv[2]
    img = bytearray(open(src, "rb").read())
    hdr_len = struct.unpack_from("<I", img, 4)[0]
    if hdr_len != HDR_LEN:
        sys.exit(f"header block is {hdr_len:#x} bytes, not {HDR_LEN:#x}: left as is")
    base = 4  # the header block starts at file offset 4
    for off, (size, raw, _) in FIELDS.items():
        have = struct.unpack_from(FMT[size], img, base + off)[0]
        if have != raw:
            sys.exit(f"header {off:#05x} holds {have:#x}, expected {raw:#x}: left as is")
    for off, (size, _, val) in FIELDS.items():
        struct.pack_into(FMT[size], img, base + off, val)
    open(dst, "wb").write(img)
    print(f"{len(FIELDS)} header fields set from the Fold's gps.cfg policy")


main()
