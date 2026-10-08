"""Hero art keeps its aspect when a details pane leaves a smaller column."""
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from luma_appkit import CoverArt, install_appkit, install_lumaui


class FlexibleCover(unittest.TestCase):
    def test_hero_reflows_without_changing_natural_size(self):
        Gtk.init(); install_appkit(); install_lumaui()
        for shape, ratio in [('album', 1), ('book', 1.5)]:
            fixed = CoverArt('Album', shape=shape, size='hero')
            cover = CoverArt('Album', shape=shape, size='hero', flexible=True)
            fixed_min, natural, *_ = fixed.measure(Gtk.Orientation.HORIZONTAL, -1)
            minimum, flexible_natural, *_ = cover.measure(Gtk.Orientation.HORIZONTAL, -1)
            self.assertEqual(natural, flexible_natural)
            self.assertEqual(fixed_min, natural)
            self.assertLess(minimum, natural)
            for width in (100, 160, 208):
                self.assertEqual(cover.measure(Gtk.Orientation.VERTICAL, width)[1], int(width * ratio))


if __name__ == '__main__':
    unittest.main()
