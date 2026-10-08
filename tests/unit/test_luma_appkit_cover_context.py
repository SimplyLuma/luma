"""Visible nested album captions follow v71 card/feature contexts without changing books."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Pango
from luma_appkit import CoverArt, install_appkit, install_lumaui


class CoverContexts(unittest.TestCase):
    def test_native_generated_captions(self):
        Gtk.init(); install_appkit(); install_lumaui()
        row = Gtk.Box()
        covers = {}
        for context in ('cover','card','feature'):
            cover = CoverArt('A Hard Day’s Night','The Beatles',presentation=context)
            covers[context] = cover;row.append(cover)
        minimum, natural, *_ = covers['cover'].measure(Gtk.Orientation.VERTICAL, -1)
        self.assertLess(minimum, natural)
        window = Gtk.Window(child=row)
        window.set_default_size(600,200);window.present()
        end=time.monotonic()+.4
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        layouts = {name:{key[0]: value for key,value in cover._layouts.items()} for name,cover in covers.items()}
        self.assertEqual(layouts['card']['title'].get_line_count(),1)
        self.assertGreater(layouts['cover']['title'].get_line_count(),1)
        self.assertEqual(layouts['card']['subtitle'].get_font_description().get_size()/Pango.SCALE,12.5)
        feature = layouts['feature']['title'].get_font_description()
        self.assertEqual(feature.get_size()/Pango.SCALE,28)
        self.assertEqual(int(feature.get_weight()),700)
        self.assertAlmostEqual(covers['feature'].get_opacity(),.85,places=2)
        self.assertEqual(covers['cover'].get_opacity(),1)
        with self.assertRaises(ValueError):CoverArt('Book',shape='book',presentation='card')
        window.close()


if __name__ == '__main__':
    unittest.main()
