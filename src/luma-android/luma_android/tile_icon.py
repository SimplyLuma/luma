# SPDX-License-Identifier: Apache-2.0
"""Cut Android's full-bleed square icons to Luma's app tile."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import stat
import struct
import tempfile


def _save_png(pixbuf, target):
    pixbuf.savev(str(target), 'png', [], [])


def _fallback(source: Path, data_root: Path | None) -> Path:
    from luma_appkit.icon_assets import prepare_application_icon
    return prepare_application_icon(source, data_root)


# Luma's app tile: inset 15/512 on every side of the canvas (the Prairie
# app icons' margin), with the spec's radius 10 on 36 of the tile (Shell's
# ICON_CORNER_RATIO).
TILE_INSET_RATIO = 15 / 512
TILE_CORNER_RATIO = 10 / 36


def tile_coverage(n: int, x: int, y: int) -> float:
    """Antialiased coverage of pixel (x, y) in an n-pixel Luma app tile."""
    radius = n * TILE_CORNER_RATIO
    dx = max(radius - (x + 0.5), (x + 0.5) - (n - radius), 0.0)
    dy = max(radius - (y + 0.5), (y + 0.5) - (n - radius), 0.0)
    if not (dx and dy):
        return 1.0
    return min(1.0, max(0.0, radius + 0.5 - math.hypot(dx, dy)))


def prepare_tile_icon(source: Path, data_root: Path | None = None) -> Path:
    """Return square artwork cut to Luma's app tile, content-addressed.

    For providers that export full-bleed square icons (Android's adaptive icon
    layers): the result carries the same silhouette as Luma's own icons on Home
    and in the switcher, at any size. The path changes with the artwork, so a
    shell texture cached for an earlier icon is never shown for a new one.
    Anything else falls back to prepare_application_icon.
    """
    source = Path(source)
    try:
        info = source.lstat()
        if not source.is_absolute() or not stat.S_ISREG(info.st_mode) or not 33 <= info.st_size <= 2*1024*1024:
            return _fallback(source, data_root)
        raw = source.read_bytes()
        if raw[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
            return _fallback(source, data_root)
        width, height = struct.unpack('>II', raw[16:24])
        if width != height or not 16 <= width <= 1024:
            return _fallback(source, data_root)
        root = data_root or Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))
        directory = root / 'luma/icons/tile-v2'
        output = directory / (hashlib.sha256(raw).hexdigest() + '.png')
        if output.is_file() and not output.is_symlink():
            return output
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf, GLib
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(raw)
        loader.close()
        artwork = loader.get_pixbuf().add_alpha(False, 0, 0, 0)
        n, stride = artwork.get_width(), artwork.get_rowstride()
        pixels = artwork.get_pixels()
        # A full-bleed square only: already-shaped artwork keeps its own form.
        if min(pixels[y*stride + x*4 + 3] for x, y in ((0, 0), (n-1, 0), (0, n-1), (n-1, n-1))) < 240:
            return _fallback(source, data_root)
        inset = round(n * TILE_INSET_RATIO)
        tile = n - 2 * inset
        scaled = artwork.scale_simple(tile, tile, GdkPixbuf.InterpType.HYPER)
        tstride = scaled.get_rowstride()
        tpixels = bytearray(scaled.get_pixels())
        corner = math.ceil(tile * TILE_CORNER_RATIO)
        for y in list(range(corner)) + list(range(tile - corner, tile)):
            for x in list(range(corner)) + list(range(tile - corner, tile)):
                offset = y*tstride + x*4 + 3
                tpixels[offset] = round(tpixels[offset] * tile_coverage(tile, x, y))
        shaped = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(tpixels)),
                                                 GdkPixbuf.Colorspace.RGB, True, 8, tile, tile, tstride)
        result = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, n, n)
        result.fill(0)
        shaped.copy_area(0, 0, tile, tile, result, inset, inset)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='.tile-', dir=directory)
        os.close(descriptor)
        try:
            _save_png(result, temporary)
            os.replace(temporary, output)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return output
    except (OSError, ValueError, ImportError):
        return source
