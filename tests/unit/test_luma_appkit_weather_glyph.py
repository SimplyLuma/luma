# SPDX-License-Identifier: Apache-2.0
"""WeatherGlyph's static GTK contract and v71 vector geometry."""
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib, Gtk
from luma_appkit import WEATHER_CONDITIONS, WeatherGlyph, install_appkit, install_lumaui
from luma_appkit.content_weather import _GEOMETRY, _path


def settle():
    end = time.monotonic() + .15
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class WeatherGlyphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            raise RuntimeError('WeatherGlyph runtime tests require a display')
        install_appkit()
        install_lumaui()

    def test_conditions_and_accessibility(self):
        glyph = WeatherGlyph('clear', night=True, label='Clear tonight')
        self.assertEqual(glyph.condition, 'moon')
        self.assertEqual(glyph.description, 'Clear tonight')
        self.assertEqual(glyph.get_accessible_role(), Gtk.AccessibleRole.IMG)
        self.assertFalse(glyph.get_focusable())
        self.assertFalse(glyph.get_can_target())
        glyph.set_condition('partly', night=True)
        self.assertEqual(glyph.condition, 'ncloud')
        for bad in ('unknown', ''):
            with self.assertRaises(ValueError):
                glyph.set_condition(bad)
        for bad in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                WeatherGlyph('sun', size=bad)

    def test_all_shapes_parse_and_stay_in_the_viewbox(self):
        self.assertEqual(len(WEATHER_CONDITIONS), 6)
        for parts in _GEOMETRY.values():
            for data, _color, stroke in parts:
                ok, bounds = _path(data).get_bounds()
                self.assertTrue(ok, data)
                self.assertGreater(bounds.get_width()+bounds.get_height(), 0)
                self.assertGreaterEqual(bounds.get_x()-stroke/2, 0)
                self.assertGreaterEqual(bounds.get_y()-stroke/2, 0)
                self.assertLessEqual(bounds.get_x()+bounds.get_width()+stroke/2, 32)
                self.assertLessEqual(bounds.get_y()+bounds.get_height()+stroke/2, 32)

    def test_render_sizes_rtl_and_palette(self):
        window = Gtk.Window()
        box = Gtk.Box()
        window.set_child(box)
        glyphs = []
        for size, kind in zip((16,22,26,32,48,64), WEATHER_CONDITIONS):
            glyph = WeatherGlyph(kind, size=size)
            box.append(glyph)
            glyphs.append(glyph)
        window.present()
        try:
            settle()
            for direction in (Gtk.TextDirection.LTR, Gtk.TextDirection.RTL):
                box.set_direction(direction)
                settle()
                for glyph in glyphs:
                    self.assertEqual((glyph.get_width(),glyph.get_height()),(glyph.size,glyph.size))
                    for role in ('sun','moon','cloud','rain'):
                        found, color = glyph.get_style_context().lookup_color('luma_weather_glyph_'+role)
                        self.assertTrue(found, role)
                        self.assertGreater(color.alpha, 0)
        finally:
            window.destroy()


if __name__ == '__main__':
    unittest.main()
