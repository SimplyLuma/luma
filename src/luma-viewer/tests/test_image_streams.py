# SPDX-License-Identifier: Apache-2.0
"""Image decoding keeps an open document handle through portal backing changes."""
import os
from pathlib import Path
import tempfile
import unittest
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib
from luma_viewer import formats

class ImageStreamTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'image.png'
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 80, 40)
        image.fill(0x7396b2ff)
        image.savev(str(self.path), 'png', [], [])
    def tearDown(self):
        self.directory.cleanup()
    def test_open_descriptor_with_deleted_backing_name(self):
        with self.path.open('rb') as handle:
            self.path.unlink()
            source = Path(f'/proc/self/fd/{handle.fileno()}')
            image, size = formats.load_image(source)
            self.assertEqual(size, (80, 40))
            self.assertEqual(bytes(image.get_pixels())[:4], bytes.fromhex('7396b2ff'))
            thumbnail = formats.image_thumbnail(source, 20)
            self.assertEqual((thumbnail.get_width(), thumbnail.get_height()), (20, 10))
    def test_corrupt_source_is_reported_then_valid_image_still_opens(self):
        broken = self.path.with_name('broken.png')
        broken.write_bytes(b'not an image')
        with self.assertRaises(GLib.Error):
            formats.load_image(broken)
        self.assertEqual(formats.load_image(self.path)[1], (80, 40))
    def test_missing_source_remains_an_error(self):
        with self.assertRaises(GLib.Error):
            formats.load_image(self.path.with_name('absent.png'))
