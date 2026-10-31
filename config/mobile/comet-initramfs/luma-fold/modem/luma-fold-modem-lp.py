#!/usr/bin/python3
"""Locate a logical partition inside an Android dynamic-partition 'super'.

  luma-fold-modem-lp.py <super device or image> <partition> [metadata offset]

Reads the LP metadata (liblp format, read-only) and prints one line per extent,
"<offset> <length>" in bytes from the start of the super device, for a
partition made only of linear extents on block device 0. Exits 1 if the
metadata is not recognised or the partition is missing, empty or not linear.

On a device, the geometry block sits at 4096 and slot 0's primary metadata at
4096 + 2 * 4096; a super_empty.img has the metadata at 4096 (pass it).
"""
import struct
import sys

GEOMETRY_MAGIC = 0x616C4467
HEADER_MAGIC = 0x414C5030
SECTOR = 512


def fail(msg):
    sys.exit("lp: " + msg)


def main():
    if len(sys.argv) not in (3, 4):
        fail(__doc__)
    path, want = sys.argv[1], sys.argv[2]
    with open(path, "rb") as f:
        if len(sys.argv) == 4:
            base = int(sys.argv[3], 0)
        else:
            f.seek(4096)
            geo = f.read(52)
            if len(geo) < 52 or struct.unpack_from("<I", geo, 0)[0] != GEOMETRY_MAGIC:
                fail("no geometry block")
            max_size, slots, block = struct.unpack_from("<III", geo, 40)
            if not (0 < slots <= 3 and 0 < max_size <= 1 << 20 and block % SECTOR == 0):
                fail("implausible geometry")
            base = 4096 + 2 * 4096		# slot 0, primary copy
        f.seek(base)
        hdr = f.read(256)
        if len(hdr) < 128 or struct.unpack_from("<I", hdr, 0)[0] != HEADER_MAGIC:
            fail("no metadata header at %#x" % base)
        major, minor, header_size = struct.unpack_from("<HHI", hdr, 4)
        if major != 10 or header_size not in (128, 256):
            fail("metadata version %d.%d, header %d" % (major, minor, header_size))
        tables_size = struct.unpack_from("<I", hdr, 44)[0]
        parts, exts = (struct.unpack_from("<III", hdr, 80 + 12 * i) for i in range(2))
        if tables_size > 1 << 20:
            fail("implausible tables size")
        f.seek(base + header_size)
        tables = f.read(tables_size)
    if len(tables) != tables_size:
        fail("short tables")

    def entries(desc, size):
        off, count, esize = desc
        if esize < size or off + count * esize > len(tables):
            fail("bad table descriptor")
        return [tables[off + i * esize:off + i * esize + esize] for i in range(count)]

    extents = entries(exts, 24)
    for p in entries(parts, 52):
        name = p[:36].split(b"\0")[0].decode("ascii", "replace")
        if name != want:
            continue
        first, num = struct.unpack_from("<II", p, 40)
        if num == 0:
            fail("%s has no extents" % want)
        if first + num > len(extents):
            fail("%s extents out of range" % want)
        for e in extents[first:first + num]:
            sectors, kind = struct.unpack_from("<QI", e, 0)
            data, source = struct.unpack_from("<QI", e, 12)
            if kind != 0 or source != 0:
                fail("%s has a non-linear extent" % want)
            print(data * SECTOR, sectors * SECTOR)
        return
    fail("no partition %s" % want)


if __name__ == "__main__":
    main()
