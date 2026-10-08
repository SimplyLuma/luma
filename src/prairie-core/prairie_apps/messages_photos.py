# SPDX-License-Identifier: Apache-2.0
"""Pictures in a conversation: their shape, and a bounded supply of decoded pixels.

A conversation can hold hundreds of photos. Each is laid out at once at its
final size, from the file's header (and its EXIF orientation), so a picture
decoding later never moves the conversation. Pixels are decoded off the main
thread at the size shown, kept in a cache bounded by bytes, and handed back
when a picture scrolls well out of view.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import struct

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

from .messages_backend import png_preview_dimensions  # noqa: E402

# The largest a picture is shown in a bubble, in logical pixels.
MAX_WIDTH = 260
MAX_HEIGHT = 320
MIN_SIDE = 96
MAX_PIXELS = 64_000_000
CACHE_BYTES = 48 * 1024 * 1024


def jpeg_orientation(path: Path) -> int:
    """The EXIF orientation of a JPEG (1-8), read from its header only; 1 when absent."""
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(128 * 1024)
    except OSError:
        return 1
    if data[:2] != b"\xff\xd8":
        return 1
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return 1
        marker = data[offset + 1]
        length = struct.unpack(">H", data[offset + 2:offset + 4])[0]
        if marker == 0xE1 and data[offset + 4:offset + 10] == b"Exif\x00\x00":
            tiff = data[offset + 10:offset + 2 + length]
            if len(tiff) < 8:
                return 1
            endian = "<" if tiff[:2] == b"II" else ">" if tiff[:2] == b"MM" else ""
            if not endian:
                return 1
            ifd = struct.unpack(endian + "I", tiff[4:8])[0]
            if ifd + 2 > len(tiff):
                return 1
            count = struct.unpack(endian + "H", tiff[ifd:ifd + 2])[0]
            for index in range(count):
                entry = ifd + 2 + index * 12
                if entry + 12 > len(tiff):
                    break
                tag = struct.unpack(endian + "H", tiff[entry:entry + 2])[0]
                if tag == 0x0112:
                    value = struct.unpack(endian + "H", tiff[entry + 8:entry + 10])[0]
                    return value if 1 <= value <= 8 else 1
            return 1
        if marker in (0xDA, 0xD9):  # image data starts; no EXIF before it
            return 1
        offset += 2 + length
    return 1


def photo_dimensions(path: Path, content_type: str = "") -> tuple[int, int]:
    """Width and height as the picture is seen (orientation applied), or (0, 0)."""
    try:
        if content_type == "image/png":
            width, height = png_preview_dimensions(path)
        else:
            _format, width, height = GdkPixbuf.Pixbuf.get_file_info(str(path))
    except (GLib.Error, OSError, TypeError, ValueError):
        return 0, 0
    if not (width > 0 and height > 0 and width * height <= MAX_PIXELS):
        return 0, 0
    if content_type in ("image/jpeg", "image/jpg", "") and jpeg_orientation(path) in (5, 6, 7, 8):
        width, height = height, width
    return width, height


def display_size(width: int, height: int, *, max_width: int = MAX_WIDTH, max_height: int = MAX_HEIGHT,
                 min_side: int = MIN_SIDE) -> tuple[int, int]:
    """The size a picture is shown at: its own shape, fitted inside the bubble's bounds.

    A panorama or a very tall screenshot keeps a readable minimum on its short
    side and is cropped from the centre rather than shrunk to a sliver.
    """
    if width <= 0 or height <= 0:
        return max_width, round(max_width * 3 / 4)
    scale = min(max_width / width, max_height / height, 1.0)
    shown_width, shown_height = width * scale, height * scale
    shown_width = min(max_width, max(min_side, shown_width))
    shown_height = min(max_height, max(min_side, shown_height))
    return round(shown_width), round(shown_height)


def decode(path: Path, width: int, height: int) -> tuple[bytes, int, int, int, bool] | None:
    """Pixels for a picture at about the size shown; safe to call off the main thread."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), max(1, width), max(1, height), True)
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    except (GLib.Error, OSError, TypeError, ValueError):
        return None
    return (pixbuf.read_pixel_bytes().get_data(), pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride(),
            pixbuf.get_has_alpha())


def texture(pixels: tuple[bytes, int, int, int, bool]) -> Gdk.Texture:
    data, width, height, stride, alpha = pixels
    memory_format = Gdk.MemoryFormat.R8G8B8A8 if alpha else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(width, height, memory_format, GLib.Bytes.new(data), stride)


class TextureCache:
    """Decoded pictures, least recently used first out, bounded by their bytes."""

    def __init__(self, limit: int = CACHE_BYTES) -> None:
        self.limit = limit
        self.size = 0
        self._items: OrderedDict[tuple, tuple[Gdk.Texture, int]] = OrderedDict()

    def get(self, key: tuple) -> Gdk.Texture | None:
        item = self._items.get(key)
        if item is None:
            return None
        self._items.move_to_end(key)
        return item[0]

    def put(self, key: tuple, value: Gdk.Texture) -> None:
        cost = value.get_width() * value.get_height() * 4
        if cost > self.limit:
            return
        if key in self._items:
            self.size -= self._items.pop(key)[1]
        self._items[key] = (value, cost)
        self.size += cost
        while self.size > self.limit and self._items:
            _key, (_texture, freed) = self._items.popitem(last=False)
            self.size -= freed

    def clear(self) -> None:
        self._items.clear()
        self.size = 0

    def __len__(self) -> int:
        return len(self._items)
