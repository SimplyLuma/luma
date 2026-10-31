#!/usr/bin/python3
"""Helpers for the Fold modem staging: the hardware configuration block and
the replay archive the s5xxx_modem driver stages into the CP.

  hwcfg  <cdt_hwid or file> <hardware_config.json> <hwcfg.bin>
         Resolve the board identity against the modem image's configuration
         table, write the 52-byte hwcfg.bin and print the RF table file name.
  replay <dds.bin> <replay_region.bin>
         Pack the live replay data as the 512 KiB ustar region the CP expects.
  handover <hwcfg.bin> <cpsha> <imei file or -> <cp_handover.bin>
         Build the 161-byte handover block the driver stages when the device
         tree carries no /chosen/config (hardware words, IMEI1/IMEI2, CP
         signature). Without an IMEI file the IMEI fields stay zero.
"""
import json
import os
import struct
import sys
import time

HWCFG_MAGIC = 0x57483553  # "S5HW"
REPLAY_SIZE = 512 * 1024


def cdt_hwid(arg):
    """The board id as the 32 hex digits of androidboot.cdt_hwid."""
    if os.path.exists(arg):
        raw = open(arg, "rb").read().rstrip(b"\0")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            text = raw.hex()
        arg = text
    arg = arg.strip().lower()
    if arg.startswith("0x"):
        arg = arg[2:]
    if len(arg) != 32 or any(c not in "0123456789abcdef" for c in arg):
        raise SystemExit(f"bad cdt_hwid {arg!r}")
    return arg


def hwcfg(cdt, table, out):
    h = cdt_hwid(cdt)
    # nibble slices of the CDT hardware id (vendor cbd layout)
    platform, product, board = int(h[0:4], 16), int(h[4:6], 16), int(h[6:8], 16)
    major, minor, variant = int(h[8:12], 16), int(h[12:14], 16), int(h[14:16], 16)
    rf_sku, modem_hw, rf_sub = int(h[16:18], 16), int(h[18:20], 16), int(h[20:24], 16)
    cfg = json.load(open(table))
    row = None
    for c in cfg["configurations"]:
        if c.get("platform") != platform or c.get("product") != product:
            continue
        for t in c["config_table"]:
            if (t["stage"], t["major"], t["minor"], t["rf_sub"], t["rf_sku"],
                    t["modem_hw"]) == (board, major, minor, rf_sub, rf_sku, modem_hw):
                row = t
    if row is None:
        raise SystemExit(f"no configuration for platform {platform} product "
                         f"{product} stage {board} rev {major}.{minor} rf_sub "
                         f"{rf_sub} rf_sku {rf_sku} modem_hw {modem_hw}")
    words = (HWCFG_MAGIC, 1, platform, row["hwinfo"], major, minor, rf_sku,
             modem_hw, rf_sub, row["rfid"], product, board, variant)
    tmp = out + ".new"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<13I", *words))
    os.replace(tmp, out)
    print(os.path.basename(row["config_file"]))
    print(f"platform {platform} product {product} stage {board} rev {major}."
          f"{minor} rf_sub {rf_sub}: rfid {row['rfid']} hwinfo {row['hwinfo']}",
          file=sys.stderr)


def ustar_header(name, size, mtime, uid, gid, uname, gname):
    h = bytearray(512)
    h[0:len(name)] = name.encode()
    h[100:108] = b"0000666\0"
    h[108:116] = b"%07o\0" % uid
    h[116:124] = b"%07o\0" % gid
    h[124:136] = b"%011o\0" % size
    h[136:148] = b"%011o\0" % mtime
    h[148:156] = b"        "
    h[156] = ord("0")
    h[257:263] = b"ustar "
    h[263:265] = b" \0"
    h[265:265 + len(uname)] = uname.encode()
    h[297:297 + len(gname)] = gname.encode()
    h[148:156] = b"%06o\0 " % sum(h)
    return bytes(h)


def replay(src, out):
    data = open(src, "rb").read()
    if len(data) > REPLAY_SIZE - 1536:
        raise SystemExit(f"{src}: {len(data)} bytes do not fit the replay region")
    # the CP rejects a stale archive, so the entry is stamped with the boot time
    blob = ustar_header("replay/dds.bin", len(data), int(time.time()),
                        1001, 1000, "radio", "system") + data
    blob += bytes(REPLAY_SIZE - len(blob))
    tmp = out + ".new"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, out)
    print(f"replay region packed from {len(data)} bytes", file=sys.stderr)


HANDOVER_SIZE = 161


def handover(hwcfg_path, cpsha_path, imei_path, out):
    w = struct.unpack("<13I", open(hwcfg_path, "rb").read())
    if w[0] != HWCFG_MAGIC or w[1] != 1:
        raise SystemExit(f"{hwcfg_path}: not an hwcfg.bin")
    _, _, platform, revision, major, minor, rf_sku, modem_hw, rf_sub, rf_config, product, board, variant = w
    blob = bytearray(HANDOVER_SIZE)
    struct.pack_into("<16I", blob, 0, 1, platform, revision, major, minor, rf_sku,
                     modem_hw, 0, 0, 0, rf_sub, rf_config, product, board, variant, 0)
    imeis = []
    if imei_path != "-" and os.path.exists(imei_path):
        imeis = [l.strip() for l in open(imei_path) if l.strip()][:2]
    for i, imei in enumerate(imeis):
        if not imei.isdigit() or len(imei) > 16:
            raise SystemExit("imei file: digits only, one per line")
        blob[64 + 16 * i:64 + 16 * i + len(imei)] = imei.encode()
    sig = open(cpsha_path, "rb").read().strip()
    if len(sig) < 64:
        raise SystemExit(f"{cpsha_path}: short CP signature")
    blob[96:160] = sig[:64]
    tmp = out + ".new"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, out)
    print(f"handover block built, {len(imeis)} IMEI(s)", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "hwcfg":
        hwcfg(*sys.argv[2:])
    elif len(sys.argv) == 4 and sys.argv[1] == "replay":
        replay(*sys.argv[2:])
    elif len(sys.argv) == 6 and sys.argv[1] == "handover":
        handover(*sys.argv[2:])
    else:
        raise SystemExit(__doc__)
