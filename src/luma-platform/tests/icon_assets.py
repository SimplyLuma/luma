"""Imported icon policy and actual PNG/cache regression coverage."""
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from luma_appkit import icon_assets as icon_assets_module
from luma_appkit.icon_assets import background_color, prepare_application_icon, artwork_bounds


def artwork(kind='circle'):
    pixels = bytearray()
    for y in range(64):
        for x in range(64):
            radius = math.hypot(x-31.5, y-31.5)
            alpha = 255 if radius < (18 if kind == 'glyph' else 32) else 0
            color = (15, 42, 70)
            if kind == 'multicolor' and x > 32:
                color = (240, 120, 10)
            if kind == 'square':
                alpha = 255
            pixels.extend((*color, alpha))
    return bytes(pixels)


def png_bytes(pixels, width=64, height=64, stride=256):
    """Encode the RGBA fixture without involving the host image-loader stack."""
    def chunk(kind, data):
        payload = kind + data
        return (struct.pack('>I', len(data)) + payload +
                struct.pack('>I', zlib.crc32(payload) & 0xffffffff))

    rows = b''.join(
        b'\0' + pixels[y * stride:y * stride + width * 4]
        for y in range(height)
    )
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) +
            chunk(b'IEND', b''))


def png_rgba(raw):
    """Decode an 8-bit RGBA PNG produced by GdkPixbuf for pixel assertions."""
    offset = 8
    compressed = bytearray()
    width = height = None
    while offset < len(raw):
        size = struct.unpack('>I', raw[offset:offset + 4])[0]
        kind = raw[offset + 4:offset + 8]
        data = raw[offset + 8:offset + 8 + size]
        offset += 12 + size
        if kind == b'IHDR':
            width, height, depth, color, compression, filtering, interlace = \
                struct.unpack('>IIBBBBB', data)
            if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise ValueError('unsupported PNG fixture encoding')
        elif kind == b'IDAT':
            compressed.extend(data)
        elif kind == b'IEND':
            break
    if width is None or height is None:
        raise ValueError('missing PNG header')

    packed = zlib.decompress(bytes(compressed))
    stride = width * 4
    rows = []
    cursor = 0
    previous = bytearray(stride)

    def paeth(left, above, upper_left):
        prediction = left + above - upper_left
        distances = (abs(prediction - left), abs(prediction - above),
                     abs(prediction - upper_left))
        return (left, above, upper_left)[distances.index(min(distances))]

    for _ in range(height):
        filter_type = packed[cursor]
        cursor += 1
        encoded = packed[cursor:cursor + stride]
        cursor += stride
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            predictors = (0, left, above, (left + above) // 2,
                          paeth(left, above, upper_left))
            if filter_type >= len(predictors):
                raise ValueError('unsupported PNG filter')
            row[index] = (value + predictors[filter_type]) & 0xff
        rows.append(bytes(row))
        previous = row
    return width, height, b''.join(rows)


class IconAssets(unittest.TestCase):
    def test_excess_canvas_is_trimmed_but_sparse_artwork_is_not(self):
        pixels=bytearray(64*64*4)
        for y in range(7,57):
            for x in range(7,57):
                pixels[(y*64+x)*4:(y*64+x)*4+4]=bytes((240,240,240,255))
        self.assertEqual(artwork_bounds(pixels,64,64,256,4),(6,6,58,58))
        self.assertIsNone(artwork_bounds(artwork('square'),64,64,256,4))
        for y in range(7,57):
            for x in range(7,57):
                if x != y: pixels[(y*64+x)*4+3]=0
        self.assertIsNone(artwork_bounds(pixels,64,64,256,4))
    def test_only_broad_consistent_background_is_extended(self):
        self.assertEqual(background_color(artwork(),64,64,256,4), (15,42,70))
        for kind in ('glyph','multicolor','square'):
            self.assertIsNone(background_color(artwork(kind),64,64,256,4), kind)
        self.assertIsNone(background_color(b'',64,64,256,4))

    def test_png_preservation_cache_update_and_invalid_input(self):
        import gi
        gi.require_version('GdkPixbuf','2.0')
        from gi.repository import GdkPixbuf, GLib

        class FixtureLoader:
            def write(self, raw):
                self.raw = raw

            def close(self):
                return True

            def get_pixbuf(self):
                return GdkPixbuf.Pixbuf.new_from_bytes(
                    GLib.Bytes.new(artwork()), GdkPixbuf.Colorspace.RGB,
                    True, 8, 64, 64, 256)

        def save_fixture(pixbuf, target):
            Path(target).write_bytes(png_bytes(
                bytes(pixbuf.get_pixels()), pixbuf.get_width(),
                pixbuf.get_height(), pixbuf.get_rowstride()))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source.png'
            source.write_bytes(png_bytes(artwork()))
            original = source.read_bytes()
            with patch.object(GdkPixbuf.PixbufLoader, 'new_with_type',
                              return_value=FixtureLoader()), \
                 patch.object(icon_assets_module, '_save_png',
                              side_effect=save_fixture):
                output = prepare_application_icon(source,root)
            self.assertNotEqual(output, source)
            width, height, pixels = png_rgba(output.read_bytes())
            self.assertEqual((width, height), (64, 64))
            self.assertEqual(tuple(pixels[:4]), (15,42,70,255))
            self.assertEqual(source.read_bytes(), original)
            identity = output.stat().st_mtime_ns
            with patch.object(GdkPixbuf.PixbufLoader,'new_with_type',side_effect=AssertionError('decoded cached icon')):
                self.assertEqual(prepare_application_icon(source,root),output)
            self.assertEqual(output.stat().st_mtime_ns,identity)
            source.write_bytes(original[:40])
            self.assertEqual(prepare_application_icon(source,root),source)
            link = root/'symlink.png';link.symlink_to(output)
            self.assertEqual(prepare_application_icon(link,root),link)
            source.write_bytes(b'not a PNG')
            self.assertEqual(prepare_application_icon(source,root),source)


if __name__ == '__main__':
    unittest.main()
