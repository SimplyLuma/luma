"""Preferred photo sizes must never force a horizontally clipped library."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
from luma_appkit import MediaGrid, MediaItem, install_appkit, install_lumaui


def settle():
    deadline = time.monotonic() + .08
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.002)


class NarrowPhotoGrid(unittest.TestCase):
    def test_large_preference_fits_a_real_scroller_and_remeasures(self):
        Gtk.init(); install_appkit(); install_lumaui()
        grid = MediaGrid([MediaItem(str(n)) for n in range(7)],
                         kind='photo', min_side=168, scrolls=False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.set_margin_start(28); body.set_margin_end(28)
        body.append(grid)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(body)
        window = Gtk.Window(child=scroll)
        window.set_default_size(360, 640); window.present(); settle()
        try:
            for preferred in (340, 527, 96, 168):
                grid.set_min_side(preferred)
                # This also verifies measure cache invalidation after zooming.
                self.assertEqual(grid.measure(Gtk.Orientation.HORIZONTAL, -1)[1], preferred)
                for width in (320, 360, 402, 500, 720, 402):
                    window.set_default_size(width, 640); settle()
                    ok, bounds = grid.compute_bounds(window)
                    self.assertTrue(ok)
                    self.assertGreaterEqual(bounds.get_x(), 28)
                    self.assertLessEqual(bounds.get_x() + bounds.get_width(), window.get_width() - 28)
                    for tile in grid._flat:
                        ok, box = tile.compute_bounds(grid)
                        self.assertTrue(ok)
                        self.assertGreaterEqual(box.get_x(), 0)
                        self.assertLessEqual(box.get_x() + box.get_width(), grid.get_width())
        finally:
            window.destroy()


if __name__ == '__main__':
    unittest.main()
