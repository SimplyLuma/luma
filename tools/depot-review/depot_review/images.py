# SPDX-License-Identifier: Apache-2.0
"""Image sizes for listing media, read from headers without decoding pixels.

Only PNG, WebP and SVG are accepted. Nothing here trusts a declared size: the
width and height come from the file itself, and anything malformed is an
error the developer sees in plain words.
"""
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET

MAX_BYTES = 20 * 1024 * 1024


class ImageError(ValueError):
    pass


def png_size(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 33 or data[12:16] != b"IHDR":
        raise ImageError("isn't a valid PNG")
    width, height = struct.unpack(">II", data[16:24])
    if not data.rstrip(b"\0").endswith(b"IEND\xaeB`\x82"):
        raise ImageError("is a PNG that ends early (the file looks cut off)")
    return width, height


def webp_size(data: bytes) -> tuple[int, int]:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ImageError("isn't a valid WebP")
    declared = struct.unpack("<I", data[4:8])[0] + 8
    if declared > len(data):
        raise ImageError("is a WebP that ends early (the file looks cut off)")
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
    elif chunk == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            raise ImageError("isn't a valid WebP")
        width, height = struct.unpack("<HH", data[26:30])
        width, height = width & 0x3FFF, height & 0x3FFF
    elif chunk == b"VP8L":
        if data[20] != 0x2F:
            raise ImageError("isn't a valid WebP")
        bits = int.from_bytes(data[21:25], "little")
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    else:
        raise ImageError("is a WebP of a kind Depot can't read")
    return width, height


_LENGTH = re.compile(r"\s*([0-9.]+)\s*(px)?\s*\Z")


def svg_size(data: bytes) -> tuple[int, int]:
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ImageError("is an SVG with a DOCTYPE or entities, which Depot doesn't accept")
    if re.search(rb"<script|\son[a-z]+\s*=|javascript:", data, re.I):
        raise ImageError("is an SVG with scripts, which Depot doesn't accept")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ImageError(f"isn't a valid SVG ({error})") from None
    if not root.tag.endswith("svg"):
        raise ImageError("isn't an SVG")
    box = root.get("viewBox")
    if box:
        parts = box.replace(",", " ").split()
        if len(parts) == 4:
            return round(float(parts[2])), round(float(parts[3]))
    w, h = _LENGTH.match(root.get("width", "") or ""), _LENGTH.match(root.get("height", "") or "")
    if w and h:
        return round(float(w.group(1))), round(float(h.group(1)))
    raise ImageError("is an SVG with no size (give it a viewBox)")


def identify(data: bytes) -> tuple[str, int, int]:
    """Return (format, width, height): format is png, webp or svg."""
    if len(data) > MAX_BYTES:
        raise ImageError("is larger than 20 MB")
    if data.startswith(b"\x89PNG"):
        return ("png", *png_size(data))
    if data[:4] == b"RIFF":
        return ("webp", *webp_size(data))
    head = data[:512].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in data[:2048]:
        return ("svg", *svg_size(data))
    raise ImageError("isn't a PNG, WebP or SVG")
