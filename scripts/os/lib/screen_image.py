#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read VM screenshots whatever format `virsh screenshot` chose, stdlib only.

libvirt names the format after what QEMU produced, not after the file name
it was given: libvirt 12 with QEMU 10 writes PNG into a file called *.ppm,
older stacks wrote binary PPM. Everything the pipeline does with a
screenshot goes through here, so no check silently depends on one format.

  screen_image.py to-png SOURCE TARGET      save SOURCE as PNG
  screen_image.py lit-fraction FILE         print the share of lit pixels
  screen_image.py same FILE FILE            exit 0 when the pixels are equal
  screen_image.py capture DOMAIN TARGET     save DOMAIN's screen (qemu:///system)

capture replaces `virsh screenshot` in the pipeline's services: under systemd
virsh runs confined (virsh_t) and may not create files on the unlabeled
pipeline volume, so every screenshot failed silently there. Through the
libvirt Python binding this process receives the image itself and writes it.
"""

import struct
import sys
import zlib


def _ppm(data):
    fields = []
    offset = 0
    while len(fields) < 4:
        while data[offset:offset + 1].isspace():
            offset += 1
        if data[offset:offset + 1] == b"#":
            offset = data.index(b"\n", offset) + 1
            continue
        end = offset
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[offset:end])
        offset = end
    magic, width, height, maxval = fields
    if magic != b"P6" or int(maxval) != 255:
        raise ValueError("only 8-bit binary PPM is supported")
    width, height = int(width), int(height)
    return width, height, data[offset + 1:offset + 1 + width * height * 3]


def _png(data):
    offset, header, idat = 8, None, []
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        offset += 12 + length
    if header is None:
        raise ValueError("PNG without IHDR")
    width, height, depth, colour, _, _, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour)
    if depth != 8 or channels is None or interlace:
        raise ValueError(f"unsupported PNG: depth {depth}, colour type {colour}, interlace {interlace}")
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rgb = bytearray()
    previous = bytearray(stride)
    for row in range(height):
        start = row * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                predictor = left if pa <= pb and pa <= pc else (up if pb <= pc else corner)
                line[i] = (line[i] + predictor) & 0xFF
        for x in range(width):
            pixel = line[x * channels:(x + 1) * channels]
            rgb += bytes(pixel[:1] * 3) if channels < 3 else bytes(pixel[:3])
        previous = line
    return width, height, bytes(rgb)


def load(path):
    """Return (width, height, RGB bytes) for a PNG or binary PPM file."""
    data = open(path, "rb").read()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(data)
    if data[:2] == b"P6":
        return _ppm(data)
    raise ValueError(f"{path}: neither PNG nor binary PPM")


def to_png(source, target):
    data = open(source, "rb").read()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        open(target, "wb").write(data)
        return
    width, height, pixels = load(source)
    rows = b"".join(b"\x00" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    open(target, "wb").write(png)


def lit_fraction(path, threshold=60):
    width, height, pixels = load(path)
    lit = sum(1 for i in range(0, len(pixels) - 2, 3)
              if pixels[i] > threshold or pixels[i + 1] > threshold or pixels[i + 2] > threshold)
    return lit / (width * height)


def stats(path):
    """Distinct colours (to 5 bits a channel) and the most common colour's share.

    A rendered application screen has many colours and no colour covering
    nearly all of it; a blank grey or black screen has a handful and one
    covering almost everything.
    """
    width, height, pixels = load(path)
    counts = {}
    for i in range(0, len(pixels) - 2, 3):
        key = (pixels[i] >> 3, pixels[i + 1] >> 3, pixels[i + 2] >> 3)
        counts[key] = counts.get(key, 0) + 1
    return len(counts), max(counts.values()) / (width * height)


def capture(domain, target):
    import libvirt  # python3-libvirt on the build host

    connection = libvirt.open("qemu:///system")
    try:
        stream = connection.newStream(0)
        try:
            connection.lookupByName(domain).screenshot(stream, 0, 0)
            chunks = []

            def sink(_stream, data, _opaque):
                chunks.append(bytes(data))
                return len(data)

            stream.recvAll(sink, None)
            stream.finish()
        except libvirt.libvirtError:
            try:
                stream.abort()
            except libvirt.libvirtError:
                pass
            raise
        data = b"".join(chunks)
        if not data:
            raise ValueError("empty screenshot")
        with open(target + ".new", "wb") as out:
            out.write(data)
        import os

        os.replace(target + ".new", target)
    finally:
        connection.close()


def main(argv):
    if len(argv) == 3 and argv[0] == "capture":
        import libvirt

        try:
            capture(argv[1], argv[2])
        except libvirt.libvirtError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0
    if len(argv) == 2 and argv[0] == "stats":
        colours, share = stats(argv[1])
        print(f"{colours} {share:.4f}")
        return 0
    if len(argv) == 3 and argv[0] == "to-png":
        to_png(argv[1], argv[2])
        return 0
    if len(argv) == 2 and argv[0] == "lit-fraction":
        print(f"{lit_fraction(argv[1]):.4f}")
        return 0
    if len(argv) == 3 and argv[0] == "same":
        return 0 if load(argv[1]) == load(argv[2]) else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, zlib.error, struct.error) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
