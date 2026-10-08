# SPDX-License-Identifier: Apache-2.0
"""A floating header reserves a transparent scroll band without painting a fake background."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GLib, Graphene, Gtk
from PIL import Image
from luma_appkit import ScrollView

class ScrollMask(unittest.TestCase):
    def test_header_band_is_transparent_then_fades(self):
        area = Gtk.DrawingArea(content_width=200, content_height=400)
        area.set_draw_func(lambda _w, cr, _width, _height: (cr.set_source_rgb(1, 0, 0), cr.paint()))
        scroll = ScrollView(area, fade_top_start=52, fade_top_end=104, fade_top_always=True)
        window = Gtk.Window(child=scroll, default_width=200, default_height=300)
        window.present()
        end = time.monotonic() + .4
        while time.monotonic() < end:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(scroll).snapshot(snapshot, scroll.get_width(), scroll.get_height())
        texture = window.get_renderer().render_texture(snapshot.to_node(), Graphene.Rect().init(0, 0, 200, 300))
        with tempfile.NamedTemporaryFile(suffix='.png') as image:
            texture.save_to_png(image.name)
            with Image.open(image.name) as image_pixels:
                self.assertIn("A", image_pixels.getbands())
                pixels = image_pixels.convert("RGBA")
        alpha = lambda y: pixels.getpixel((50, y))[3]
        self.assertEqual(alpha(25), 0)
        self.assertTrue(115 <= alpha(78) <= 140)
        self.assertEqual(alpha(130), 255)
        default = ScrollView()
        self.assertEqual((default._fade_top_start, default._fade_top_end, default._fade_top_always), (0, 22, False))
        window.close()
        with self.assertRaises(ValueError):
            ScrollView(fade_top_start=52, fade_top_end=30)

if __name__ == '__main__':
    unittest.main()
