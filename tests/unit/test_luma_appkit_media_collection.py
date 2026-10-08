# SPDX-License-Identifier: Apache-2.0
"""Live collection cover: activation, responsive loader and visible lower scrim."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GLib, Graphene, Gtk
from PIL import Image
from luma_appkit import MediaCollectionCard, install_appkit


def settle(seconds=.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class CollectionCard(unittest.TestCase):
    def test_click_resize_and_gradient(self):
        install_appkit()
        pixels = GLib.Bytes.new(bytes([255, 255, 255, 255]) * 4)
        white = Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8, pixels, 8)
        clicks = []
        card = MediaCollectionCard(white, title='2026', detail='22 photos',
                                   on_activate=lambda: clicks.append(True))
        window = Gtk.Window(default_width=360, default_height=225, child=card)
        window.present(); settle()
        self.assertTrue(card.activate())
        settle()
        self.assertEqual(clicks, [True])
        self.assertAlmostEqual(card.get_width() / card.get_height(), 1.6, delta=.01)
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(card).snapshot(snapshot, card.get_width(), card.get_height())
        texture = window.get_renderer().render_texture(snapshot.to_node(),
            Graphene.Rect().init(0, 0, card.get_width(), card.get_height()))
        with tempfile.NamedTemporaryFile(suffix='.png') as image:
            texture.save_to_png(image.name)
            with Image.open(image.name) as image_pixels:
                pixels = image_pixels.convert("RGB")
        def lightness(fraction):
            return pixels.getpixel((card.get_width() // 2, int(card.get_height() * fraction)))[0]
        self.assertGreater(lightness(.4), 250)
        self.assertTrue(190 < lightness(.65) < 215)
        self.assertTrue(95 < lightness(.98) < 120)
        with tempfile.NamedTemporaryFile(suffix='.png') as image:
            white.save_to_png(image.name)
            card.set_picture(image.name)
            settle(.6)
            self.assertIsNotNone(card.picture._picture)
            old_bucket = card.picture._wanted[1]
            window.set_default_size(720, 450); settle(.6)
            self.assertEqual(card.get_width(), 720)
            self.assertEqual(card.get_height(), 450)
            self.assertGreater(card.picture._wanted[1], old_bucket)
        window.close()

    def test_invalid_aspect(self):
        for aspect in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                MediaCollectionCard(aspect=aspect)


if __name__ == '__main__':
    unittest.main()
