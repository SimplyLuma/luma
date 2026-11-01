# SPDX-License-Identifier: Apache-2.0
import math
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from luma_android.tile_icon import TILE_CORNER_RATIO, prepare_tile_icon, tile_coverage


def png(alpha_at, n=64):
    rows = b''.join(b'\0' + b''.join(bytes((15, 42, 70, alpha_at(x, y))) for x in range(n)) for y in range(n))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', n, n, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class TileIcon(unittest.TestCase):
    def test_tile_corner_is_cut_and_edges_kept(self):
        n = 100
        self.assertEqual(tile_coverage(n, 0, 0), 0.0)
        self.assertEqual(tile_coverage(n, 50, 0), 1.0)
        self.assertEqual(tile_coverage(n, 0, 50), 1.0)
        inner = math.ceil(n * TILE_CORNER_RATIO)
        self.assertEqual(tile_coverage(n, inner, inner), 1.0)

    def test_full_bleed_square_is_cut_to_the_tile_and_content_addressed(self):
        try:
            import gi
            gi.require_version('GdkPixbuf', '2.0')
            from gi.repository import GdkPixbuf
        except (ImportError, ValueError):
            self.skipTest('GdkPixbuf unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'android.png'
            source.write_bytes(png(lambda x, y: 255))
            output = prepare_tile_icon(source, root)
            self.assertIn('luma/icons/tile-v2', str(output))
            shaped = GdkPixbuf.Pixbuf.new_from_file(str(output))
            pixels, stride = shaped.get_pixels(), shaped.get_rowstride()
            alpha = lambda x, y: pixels[y * stride + x * 4 + 3]
            self.assertEqual(alpha(0, 0), 0)        # margin
            self.assertEqual(alpha(32, 32), 255)    # body
            self.assertEqual(alpha(3, 3), 0)        # rounded corner
            self.assertEqual(prepare_tile_icon(source, root), output)

    def test_shaped_artwork_keeps_its_own_form(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'circle.png'
            source.write_bytes(png(lambda x, y: 255 if math.hypot(x - 31.5, y - 31.5) < 32 else 0))
            from unittest.mock import patch
            with patch('luma_android.tile_icon._fallback', return_value=source) as fallback:
                self.assertEqual(prepare_tile_icon(source, root), source)
            fallback.assert_called_once()


if __name__ == '__main__':
    unittest.main()
