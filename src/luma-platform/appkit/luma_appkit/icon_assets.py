"""Prepare imported raster application icons once, outside the rendering path.

Original artwork is never modified. Ambiguous artwork keeps the neutral system
tile. This is deliberately not dominant-colour extraction: a coloured logo on
transparent paper is not evidence of a background.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import stat
import struct
import tempfile


def _save_png(pixbuf, target):
    """Keep the external image-encoder boundary replaceable in unit tests."""
    pixbuf.savev(str(target), 'png', [], [])


def artwork_bounds(pixels, width, height, stride, channels):
    """Trim excessive transparent canvas around a broad, nearly square tile.

    Sparse glyphs and intentionally narrow artwork keep their original canvas.
    One sample pixel is retained for antialiasing and the publisher's shadow.
    """
    if channels != 4 or stride < width * 4 or len(pixels) < (height-1)*stride+width*4:
        return None
    points = [(x, y) for y in range(height) for x in range(width)
              if pixels[y*stride+x*4+3] >= 16]
    if not points:
        return None
    left, top = min(x for x,y in points), min(y for x,y in points)
    right, bottom = max(x for x,y in points)+1, max(y for x,y in points)+1
    w, h = right-left, bottom-top
    if not (.5*width <= w <= .90*width and .5*height <= h <= .90*height):
        return None
    if abs(w-h) > max(w,h)*.15 or len(points) < .60*w*h:
        return None
    return max(0,left-1), max(0,top-1), min(width,right+1), min(height,bottom+1)


def background_color(pixels: bytes, width: int, height: int, stride: int,
                     channels: int) -> tuple[int, int, int] | None:
    """Require a broad, opaque silhouette with a consistent outer colour."""
    if width < 16 or height < 16 or abs(width - height) > max(width, height) / 10:
        return None
    if channels != 4 or stride < width * 4 or len(pixels) < (height-1)*stride + width*4:
        return None

    def pixel(x, y):
        offset = y * stride + x * 4
        return tuple(pixels[offset:offset+4])

    # Fully opaque squares already supply their background. Sparse glyphs must
    # not turn into enormous solid tiles of their foreground colour.
    opaque = sum(pixel(x, y)[3] >= 240
                 for y in range(height) for x in range(width))
    if not .60 * width * height <= opaque <= .96 * width * height:
        return None
    samples = []
    for ray in range(32):
        angle = ray * math.tau / 32
        for step in range(8):
            radius = .49 - step * .02
            x = min(width-1, max(0, int((.5 + radius*math.cos(angle))*width)))
            y = min(height-1, max(0, int((.5 + radius*math.sin(angle))*height)))
            value = pixel(x, y)
            if value[3] >= 240:
                samples.append(value[:3])
                break
    if len(samples) < 30:
        return None
    median = tuple(sorted(p[c] for p in samples)[len(samples)//2] for c in range(3))
    matching = [p for p in samples if max(abs(p[c]-median[c]) for c in range(3)) <= 24]
    if len(matching) < 28:
        return None
    return tuple(round(sum(p[c] for p in matching)/len(matching)) for c in range(3))


def prepare_application_icon(source: Path, data_root: Path | None = None) -> Path:
    """Return a persistent, content-addressed PNG, or the untouched original.

    Only bounded PNG files are admitted. No external resources, display/GPU,
    resident helper or per-frame pixel analysis is involved. The digest includes
    the algorithm version; changing artwork naturally invalidates the cache.
    """
    source = Path(source)
    if not source.is_absolute():
        return source
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 33 <= info.st_size <= 2*1024*1024:
                return source
            raw = stream.read(2*1024*1024 + 1)
        if len(raw) > 2*1024*1024 or raw[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
            return source
        width, height = struct.unpack('>II', raw[16:24])
        if not 16 <= width <= 1024 or not 16 <= height <= 1024:
            return source
        root = data_root or Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))
        directory = root / 'luma/icons/prepared-v2'
        digest = hashlib.sha256(raw).hexdigest()
        output = directory / (digest + '.png')
        if output.is_file() and not output.is_symlink():
            return output
        unchanged = directory / (digest + '.unchanged')
        if unchanged.is_file():
            return source
        # Lazy GI import: callers and pure policy tests do not need a display.
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf, GLib
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        try:
            loader.write(raw)
            loader.close()
        except GLib.Error:
            return source
        artwork = loader.get_pixbuf()
        sample = artwork.scale_simple(64, 64, GdkPixbuf.InterpType.BILINEAR)
        bounds = artwork_bounds(sample.get_pixels(), 64, 64, sample.get_rowstride(), sample.get_n_channels())
        cropped = bounds is not None
        if cropped:
            left, top, right, bottom = bounds
            x, y = left*width//64, top*height//64
            width, height = right*width//64-x, bottom*height//64-y
            artwork = artwork.new_subpixbuf(x,y,width,height)
            sample = artwork.scale_simple(64,64,GdkPixbuf.InterpType.BILINEAR)
        color = background_color(sample.get_pixels(), 64, 64, sample.get_rowstride(),
                                 sample.get_n_channels()) if not cropped and abs(width-height) <= max(width,height)/10 else None
        if color is None and not cropped:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            unchanged.touch(exist_ok=True)
            return source
        result = artwork
        if color is not None:
            result = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
            result.fill((color[0]<<24) | (color[1]<<16) | (color[2]<<8) | 255)
            artwork.composite(result, 0, 0, width, height, 0, 0, 1, 1,
                              GdkPixbuf.InterpType.NEAREST, 255)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='.prepare-', dir=directory)
        os.close(descriptor)
        try:
            _save_png(result, temporary)
            os.replace(temporary, output)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return output
    except (OSError, ValueError, ImportError):
        return source
